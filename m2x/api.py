import sys
import json
import uuid
import time
import platform
import threading

from requests import session
from paho.mqtt.client import MQTT_ERR_SUCCESS, Client as MQTTClient

from m2x import version
from m2x.utils import DateTimeJSONEncoder


PYTHON_VERSION = '{major}.{minor}.{micro}'.format(
    major=sys.version_info.major,
    minor=sys.version_info.minor,
    micro=sys.version_info.micro
)

USER_AGENT = 'M2X-Python/{version} python/{python_version} ({platform})'\
                .format(version=version,
                        python_version=PYTHON_VERSION,
                        platform=platform.platform())


class APIBase(object):
    PATH = '/'

    def __init__(self, key, client, **kwargs):
        self.apikey = key
        self.client = client
        self._locals = threading.local()

    def url(self, *parts):
        parts = (self.client_endpoint(), self.PATH) + parts
        return '/'.join(map(lambda p: p.strip('/'), filter(None, parts)))

    def get(self, path, **kwargs):
        return self.request(path, **kwargs)

    def post(self, path, **kwargs):
        return self.request(path, method='POST', **kwargs)

    def put(self, path, **kwargs):
        return self.request(path, method='PUT', **kwargs)

    def delete(self, path, **kwargs):
        return self.request(path, method='DELETE', **kwargs)

    def patch(self, path, **kwargs):
        return self.request(path, method='PATCH', **kwargs)

    def head(self, path, **kwargs):
        return self.request(path, method='HEAD', **kwargs)

    def options(self, path, **kwargs):
        return self.request(path, method='OPTIONS', **kwargs)

    @property
    def last_response(self):
        return getattr(self._locals, 'last_response', None)

    @last_response.setter
    def last_response(self, value):
        self._locals.last_response = value

    def to_json(self, value):
        return json.dumps(value, cls=DateTimeJSONEncoder)

    def client_endpoint(self):
        raise NotImplementedError('Implement in subclass')

    def request(self, path, apikey=None, method='GET', **kwargs):
        raise NotImplementedError('Implement in subclass')


class Response(object):
    def __init__(self, response, raw, status, headers, json):
        self.response = response
        self.raw = raw
        self.status = status
        self.headers = headers
        self.json = json

    @property
    def success(self):
        return self.status >= 200 and self.status < 300

    @property
    def client_error(self):
        return self.status >= 400 and self.status < 500

    @property
    def server_error(self):
        return self.status >= 500

    @property
    def error(self):
        return self.client_error or self.server_error


class HTTPResponse(Response):
    def __init__(self, response):
        try:
            json = response.json()
        except ValueError:
            json = None
        super(HTTPResponse, self).__init__(
            response=response,
            raw=response.content,
            status=response.status_code,
            headers=response.headers,
            json=json
        )


class MQTTResponse(Response):
    def __init__(self, response):
        super(MQTTResponse, self).__init__(
            response=response,
            raw=response,
            status=response['status'],
            headers={},
            json=response
        )


class HTTPAPIBase(APIBase):
    @property
    def session(self):
        if not hasattr(self, '_session'):
            sess = session()
            sess.headers.update({'X-M2X-KEY': self.apikey,
                                 'Content-type': 'application/json',
                                 'Accept-Encoding': 'gzip, deflate',
                                 'User-Agent': USER_AGENT})
            self._session = sess
        return self._session

    def client_endpoint(self):
        return self.client.endpoint

    def request(self, path, apikey=None, method='GET', **kwargs):
        url = self.url(path)

        if apikey:
            kwargs.setdefault('headers', {})
            kwargs['headers']['X-M2X-KEY'] = apikey

        if method in ('PUT', 'POST', 'DELETE', 'PATCH') and kwargs.get('data'):
            kwargs['data'] = self.to_json(kwargs['data'])

        resp = self.session.request(method, url, **kwargs)
        self.last_response = HTTPResponse(resp)

        if resp.status_code == 204:
            return None
        else:
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return resp


class MQTTAPIBase(APIBase):
    def __init__(self, key, client, timeout=600, **kwargs):
        self.timeout = timeout
        super(MQTTAPIBase, self).__init__(key, client, **kwargs)

    @property
    def mqtt(self):
        if not hasattr(self, '_mqtt_client'):
            self.responses = {}
            self.ready = False
            client = MQTTClient()
            client.username_pw_set(self.apikey)
            client.on_connect = self._on_connect
            client.on_message = self._on_message
            client.on_subscribe = self._on_subscribe
            client.connect(self.client_endpoint().replace('mqtt://', ''))
            self._mqtt_client = client
            # Start the loop and wait for the connection to be ready
            self._mqtt_client.loop_start()
            while not self.ready:
                time.sleep(.1)
        return self._mqtt_client

    def _on_connect(self, client, userdata, flags, rc):
        client.subscribe('m2x/{apikey}/responses'.format(apikey=self.apikey))

    def _on_message(self, client, userdata, msg):
        msg = json.loads(msg.payload)
        self.responses[msg['id']] = msg

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        self.ready = True

    def client_endpoint(self):
        return self.client.mqtt_endpoint

    def url(self, *parts):
        parts = (self.PATH,) + parts
        return '/{0}'.format('/'.join(map(lambda p: p.strip('/'),
                                          filter(None, parts))))

    def wait_for_response(self, msg_id):
        timeout = self.timeout

        while msg_id not in self.responses:
            time.sleep(.1)
            if timeout is not None:
                if timeout > 0:
                    timeout -= 1
                else:
                    break
        if msg_id in self.responses:
            response = self.responses.pop(msg_id)
            self.last_response = MQTTResponse(response)
            if 'body' in response:
                response = response['body']
            return response

    def request(self, path, apikey=None, method='GET', **kwargs):
        msg_id = uuid.uuid4().hex
        msg = self.to_json({
            'id': msg_id,
            'method': method,
            'resource': self.url(path),
            'body': kwargs.get('data') or kwargs.get('params') or {}
        })
        status, mid = self.mqtt.publish('m2x/{apikey}/requests'.format(
            apikey=apikey or self.apikey
        ), payload=msg)
        if status == MQTT_ERR_SUCCESS:
            return self.wait_for_response(msg_id)
