from functools import wraps
from datetime import date, datetime
from json import JSONEncoder

from iso8601 import iso8601


class DateTimeJSONEncoder(JSONEncoder):
    def default(self, value):
        if isinstance(value, (datetime, date)):
            return to_iso(value)
        else:
            return super(DateTimeJSONEncoder, self).default(value)


def memoize(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        name = '_{0}'.format(func.__name__)
        if not hasattr(self, name):
            result = func(self, *args, **kwargs)
            setattr(self, name, result)
            return result
        return getattr(self, name)
    return wrapper


def pmemoize(func):
    return property(memoize(func))


def process_values(values, timestamp_name='at'):
    return list(map(lambda v: process_values(v, timestamp_name), values))


def process_value(value, timestamp_name='at'):
    if isinstance(value, tuple):
        if len(value) == 2:
            value = {timestamp_name: value[0], 'value': value[1]}
        elif len(value) == 1:
            value = {'value': value[0]}
    elif not isinstance(value, dict):
        value = {'value': value}

    # Ensure a datetime in the value, the server will ensure local
    # datetime if no value is passed anyway, but since the server
    # doesn't return the value created, there's no way to get it unless
    # all the values are requested again
    value[timestamp_name] = \
        to_iso(value.get(timestamp_name) or datetime.now())
    return value


def to_iso(dtime):
    if not isinstance(dtime, (date, datetime)):
        dtime = iso8601.parse_date(dtime)
    return dtime.replace(tzinfo=iso8601.UTC)\
                .strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def tags_to_server(tags):
    if not isinstance(tags, (list, tuple)):
        tags = [tags]
    return ','.join(tags)


def from_server(name, value):
    if name == 'tags':
        value = value.split(',')
    else:
        try:
            value = iso8601.parse_date(value)
        except iso8601.ParseError:
            pass
    return value


def to_server(name, value):
    if name == 'tags':
        value = tags_to_server(value)
    elif isinstance(value, (datetime, date)):
        value = to_iso(value)
    return value


def attrs_from_server(attrs):
    return dict((name, from_server(name, value))
                    for name, value in attrs.items())


def attrs_to_server(attrs):
    return dict((name, to_server(name, value))
                    for name, value in attrs.items())
