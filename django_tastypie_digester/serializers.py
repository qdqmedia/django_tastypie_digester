"""Serializers"""

import sys


try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        sys.stderr.write('ERROR: Please install the `json` or `simplejson` module')
        sys.exit(-1)


class SerializerInterface(object):
    """
    Any custom serializer has to implement this api.
    """
    def encode(self, data):
        raise NotImplementedError

    def decode(self, data):
        raise NotImplementedError


class JsonSerializer(SerializerInterface):
    """
    Simple JSON serializer
    """
    def encode(self, data):
        return json.dumps(data)

    def decode(self, data):
        return json.loads(data)
