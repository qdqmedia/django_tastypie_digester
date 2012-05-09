import pprint
import urlparse
import urllib
from django.conf import settings
from django.utils import simplejson
import requests
import sys
from django_tastypie_digester.serializers import SerializerInterface
from .serializers import JsonSerializer
from .exceptions import BadHttpStatus, ResourceIdMissing, TooManyResources


# TODO: refactor like QuerySet if possible
class EndpointProxy(object):
    """
    Proxy object to a service endpoint

    E.g. api.mailing
    """
    def __init__(self, api, endpoint_url, schema_url):
        self._api = api
        self._endpoint_url = endpoint_url
        self._schema_url = schema_url
        self._resource = filter(bool, endpoint_url.split('/'))[-1]

    def __repr__(self):
        return '<EndpointProxy %s>' % self._api._get_url(self._resource)

    def _get_url(self):
        return '%s%s' % (self._api.base_url, self._endpoint_url)

    def one(self, id=None, **kw):
        return self._api.one(self._resource, id, **kw)

    def many(self, *ids, **kw):
        return self._api.many(self._resource, *ids, **kw)

    def find(self, **kw):
        return self._api.find(self._resource, **kw)

    def add(self, **kw):
        return self._api.add(self._resource, **kw)

    def update(self, id, **kw):
        return self._api.update(self._resource, id, **kw)

    def delete(self, id):
        return self._api.delete(self._resource, id)


class Resource(object):
    """
    A fetched resource

    E.g. api.mailing.one(1)
    """
    def __init__(self, resource, type, id, url):
        self._resource = resource
        self._type = type
        self._id = id
        self._url = url

    def __repr__(self):
        return '<Resource %s: %s>' % (self._url, self._resource)

    def __getattr__(self, attr):
        if attr in self._resource:
            return self._resource[attr]
        else:
            raise AttributeError(attr)

    def __getitem__(self, item):
        if item in self._resource:
            return self._resource[item]
        else:
            raise KeyError(item)

    def __contains__(self, attr):
        return attr in self._resource


class ResourceProxy(object):
    """
    Proxy object to not evaluated resource
    (like resource property pointing to another resource)

    It lazily fetches data.

    E.g. api.mailing.one(1).user
    """
    def __init__(self, url, service, api):
        self._url = url
        self._service = service
        self._api = api
        self._type, id = self._service.get_resource_ident(self._url)
        self._id = int(id)
        self._resource = None

    def __repr__(self):
        if self._resource:
            return repr(self._resource)
        else:
            return '<ResourceProxy %s/%s>' % (self._type, self._id)

    def __getattr__(self, attr):
        return getattr(self._get(), attr)

    def __getitem__(self, item):
        return self._get()[item]

    def __contains__(self, attr):
        return attr in self._get()

    def _get(self):
        """Load the resource

        Do nothing if already loaded.
        """
        if not self._resource:
            self._resource = self._api.one(self._type, self._id)
        return self._resource


# TODO: zpruhlednit funkcnost ResourceListMixin, udelat z neho list
# TODO: dat na ResourceListMixin napr. i vysledek api.many()
class ResourceListMixin(object):
    """
    Helper for lists.

    Used only for some lists and not in very clear way.
    #TODO!!!
    """
    def values(self):
        return [r._resource for r in self[:]]

    def values_list(self, *fields, **kw):
        if 'flat' in kw and kw['flat'] is True:
            if len(fields) != 1:
                raise Exception('Can\'t flatten if more than 1 field')
            field = fields[0]
            return [getattr(r, field) for r in self[:]]
        else:
            return [tuple(getattr(r, f) for f in fields) for r in self[:]]

    def __getitem__(self, index):
        raise NotImplementedError()


class SearchResponse(ResourceListMixin):
    """
    A service response containing multiple resources

    E.g. api.mailing.find(...)
    """
    def __init__(self, api, type, meta, resources, kw={}):
        self._api = api
        self._type = type
        self._total_count = meta['total_count']
        self._resources = dict(enumerate(resources))
        self._kw = kw

    def __repr__(self):
        return '<SearchResponse %s (%s/%s)>' % (self._type, len(self._resources), self._total_count)

    def __len__(self):
        return self._total_count

    def _parse_resource(self, resource):
        """Parses a raw resource as returned by the service, replace related
           resource URLs with ResourceProxy objects.
        """

        url = resource['resource_uri']
        del resource['resource_uri']

        for attr, value in resource.items():
            if self._api._service.is_resource_url(value):
                resource[attr] = ResourceProxy(value, self._api._service, self)
            elif isinstance(value, list):
                resource[attr] = ListProxy(value, self._api._service, self)

        resource_name, resource_id = self._api._service.get_resource_ident(url)
        return Resource(resource, resource_name, resource_id, url)

    def _parse_resources(self, resources):
        return map(self._parse_resource, resources)

    def __getitem__(self, index):
        if isinstance(index, slice):
            offset = index.start or 0
            limit = len(self) - offset
            missing = [index for index in range(offset, offset + limit) if index not in self._resources]

            if missing:
                req_offset = min(missing)
                req_limit = max(missing) - req_offset + 1
                kw = self._kw.copy()
                kw['offset'] = req_offset
                kw['limit'] = req_limit
                response = self._api._get(self._type, **kw)
                resources = self._api._parse_resources(response['objects'])
                for index, resource in enumerate(resources):
                    self._resources[req_offset + index] = resource

            return [self._resources[i] for i in range(offset, offset + limit)]

        else:
            if index >= len(self):
                raise IndexError(index)
            elif index not in self._resources:
                kw = self._kw.copy()
                kw['offset'] = index
                kw['limit'] = 1
                response = self._api._get(self._type, **kw)
                resource = self._api._parse_resource(response['objects'][0])
                self._resources[index] = resource
            return self._resources[index]


class ListProxy(ResourceListMixin):
    """
    List of connected ToMany items.

    Acts like a `list` but resolves ResourceProxy objects on access.

    E.g. api.user.one(1).mailings
    """
    def __init__(self, list, service, api):
        self._list = list
        self._service = service
        self._api = api

    def __repr__(self):
        return pprint.pformat(self._list)

    def _parse_item(self, item):
        if self._service.is_resource_url(item):
            return ResourceProxy(item, self._service, self._api)
        else:
            return item

    def _evaluate_item(self, item):
        item = self._parse_item(item)
        if isinstance(item, ResourceProxy):
            return item._get()
        return item

    def __getitem__(self, index):
        item = self._list[index]
        if not item:
            return []
        if isinstance(item, list):
            # index is a slice object
            slice = index
            items = map(self._parse_item, item)
            missing = {}
            for index, item in enumerate(items):
                if isinstance(item, ResourceProxy):
                    if item._resource:
                        items[index] = item._resource
                    else:
                        type = item._type
                        if type not in missing:
                            missing[type] = {}
                        # We assume a list only contains unique IDs otherwise, we lose the list index of duplicate IDs.
                        missing[type][item._id] = index
            for type in missing:
                ids = missing[type].keys()
                resources = self._api.many(type, *ids)
                for id, resource in resources.items():
                    index = missing[type][int(id)]
                    items[index] = resource
            self._list[slice] = items
            return items
        item = self._evaluate_item(item)
        self._list[index] = item
        return item


class Resource(object):
    """
    A fetched resource

    Its data available as properties.

    E.g. api.mailing.one(1)
    """
    def __init__(self, data, name, id, url):
        self._data = data
        self._name = name
        self._id = id
        self._url = url

    def __repr__(self):
        return '<Resource %s: %s>' % (self._url, self._data)

    def __getattr__(self, attr):
        if attr in self._data:
            return self._data[attr]
        else:
            raise AttributeError(attr)

    def __getitem__(self, item):
        if item in self._data:
            return self._data[item]
        else:
            raise KeyError(item)

    def __contains__(self, attr):
        return attr in self._data

    @classmethod
    def manufacture(cls, api, data):
        """
        Manufactures Resource from raw data returned by server.

        Replace related resource URLs with ResourceProxy objects.

        :param: api: Api
        :param: data: dict
        :returns: Resource
        """
        assert isinstance(api, Api)
        assert isinstance(data, dict)
        url = data['resource_uri']
        del data['resource_uri']

        for attr, value in data.items():
            if api.parser.is_resource_url(value):
                data[attr] = ResourceProxy(value, api.parser, api)
            elif isinstance(value, list):
                data[attr] = ListProxy(value, api.parser, api)

        resource_name, resource_id = api.parser.get_resource_ident(url)
        return Resource(data, resource_name, resource_id, url)

    @classmethod
    def manufacture_many(cls, api, data):
        """
        Manufactures Resources from raw data list.

        :param: api: Api
        :param: data: iterable
        :returns: list(Resource)
        """
        assert isinstance(api, Api)
        assert hasattr(data, '__iter__')
        return [Resource.manufacture(api, item) for item in data]



class EndpointProxy(object):
    """
    Proxy object to a service endpoint

    E.g. api.mailing
    """
    def __init__(self, api, endpoint_url, schema_url):
        assert isinstance(api, Api)
        self._api = api
        self._endpoint_url = endpoint_url
        self._schema_url = schema_url
        self._resource_name = filter(bool, endpoint_url.split('/'))[-1]

    def __repr__(self):
        return '<EndpointProxy %s>' % self._get_url()

    def _get_url(self):
        return self._api._get_url(self._resource_name)

    def _get_schema_url(self):
        return '%s%s' % (self._api.parser.base_url, self._schema_url)

    def get_schema(self):
        """
        Returns endpoint schema.

        :returns: dict
        """
        return self._api._get_by_url(self._get_schema_url())

    def get(self, id=None, **kwargs):
        """
        Returns one resource.

        Specified by id or by django QuerySet filter attributes.

        :returns: Resource
        """
        if id:
            data = self._api._get(self._resource_name, id, **kwargs)
            return Resource.manufacture(self._api, data)
        if not kwargs:
            raise ResourceIdMissing
        response = self.filter(self._resource_name, **kwargs)
        if len(response) != 1:
            raise TooManyResources
        return response[0]

    def get_many(self, *ids, **kwargs):
        """
        Returns more resources.

        Specified by ids.

        :returns: dict(str(resource id): Resource)
        """
        id = 'set/' + ';'.join(map(str, ids))
        data = self._api._get(self._resource_name, id)
        resources = Resource.manufacture_many(self._api, data['objects'])
        # Transform a list of Resource in a dict using resource ID as key
        resources = dict([(r.id, r) for r in resources])
        # Add not found IDs to the dict
        if 'not_found' in data:
            for id in data['not_found']:
                resources[int(id)] = None
        return resources

    def filter(self, **kwargs):
        """
        Returns filtered resources.

        Specified by django QuerySet filter attributes.

        :returns: SearchResponse
        """
        data = self._api._get(self._resource_name, **kwargs)
        meta = data['meta']
        resources = Resource.manufacture_many(self._api, data['objects'])
        return SearchResponse(self, self._resource_name, meta, resources, kwargs)

    def add(self, **kwargs):
        """
        Adds resource to this endpoint and returns it.

        Issues POST to endpoint url with resource parameters given as **kwargs.

        :returns: Resource
        """
        url = self._get_url()
        headers = {'content-type': 'application/json'}
        response = self._api._request(url, request=requests.post, data=simplejson.dumps(kwargs), headers=headers)
        if response.status_code != 201:
            self._api._raise_error(response)
        data = self._api._get_by_url(response.headers['location'])
        return Resource.manufacture(self._api, data)


class Parser(object):
    """
    Service url parser.
    """
    def __init__(self, url):
        self.url = url
        self.base_url, self.base_path = self._get_url_parts(url)

    def _get_url_parts(self, url):
        """
        Extracts the base URL and the base path from the service URL.

        E.g.
        self._get_url_parts('http://foo.bar/1/') -> ('http://foo.bar', '/1/')

        :returns: 2-tuple (str, str)
        """
        proto, host, path = urlparse.urlsplit(url)[0:3]
        return '%s://%s' % (proto, host), path

    def is_resource_url(self, url):
        """
        Returns True if `url` is a valid resource URL

        :returns: bool
        """
        return isinstance(url, basestring) and url.startswith(self.base_path)

    def get_resource_ident(self, url):
        """
        Parses a resource URL and returns a tuple of (resource_name, resource_id)

        E.g.
        self.get_resource_ident('http://foo.bar/1/resource/1') -> ('resource', '1')

        :returns: 2-tuple (str, str)
        """
        return url.split('/')[-3:-1]


class Api(object):
    """
    The TastyPie client

    E.g.
    api = Api('http://127.0.0.1:8000/api/v1/', auth=('martin', '***'))
    """
    def __init__(self, service_url, serializer=None, auth=None, config={}):
        self._request_auth = auth
        self._request_config = config
        if settings.DEBUG:
            self._request_config['verbose'] = sys.stdout
        self.parser = Parser(service_url)
        self._serializer = serializer or JsonSerializer()
        assert isinstance(self._serializer, SerializerInterface)
        self._endpoints = self._get() # The API endpoint should return resource endpoints list.

    def __getattr__(self, name):
        """
        Summons endpoints.

        :returns: EndpointProxy
        """
        return self.get_endpoint(name)

    def get_endpoint(self, name):
        """
        Returns endpoint proxy object for desired resource.

        E.g.
        api.get_endpoint('mailing') -> EndpointProxy to 'http://127.0.0.1:8000/api/v1/mailing'

        :returns: EndpointProxy
        """
        if name in self._endpoints:
            return EndpointProxy(self, self._endpoints[name]['list_endpoint'], self._endpoints[name]['schema'])
        else:
            raise AttributeError(name)

    def get_endpoints(self):
        """
        Returns available endpoints

        E.g.
        {
            u'mailing': <EndpointProxy http://127.0.0.1:8000/api/v1/mailing/>,
            u'profile': <EndpointProxy http://127.0.0.1:8000/api/v1/profile/>,
            u'user': <EndpointProxy http://127.0.0.1:8000/api/v1/user/>
        }

        :returns: dict(unicode, EndpointProxy)
        """
        return dict((item, self.get_endpoint(item)) for item in self._endpoints.keys())

    def _get_url(self, resource_name=None, resource_id=None, **kwargs):
        """Generate an URL

        1. The service URL is used as the base string (e.g. "/api/1/")
        2. If a `resource_name` is given, it is appended (e.g. "/api/1/country/")
            2.1. If an `resource_id` is given, it is appended (e.g. "/api/1/country/2/")
        3. If keyword arguments are given, construct a query string and append it e.g.
           kwargs = dict(foo=42, bar='test') => '/api/1/resource_name/?foo=42&bar=test

        :returns: str
        """
        url = self.parser.url
        if resource_name is not None:
            url += '%s/' % resource_name
            if resource_id is not None:
                url += '%s/' % resource_id
        if kwargs:
            for key, value in kwargs.items():
                if isinstance(value, basestring):
                    kwargs[key] = value.encode('utf-8')
            url += '?' + urllib.urlencode(kwargs)
        return url

    def _request(self, url, request=requests.get, data=None, headers=None, **kwargs):
        """
        Does the request.

        :returns: requests.models.Response
        """
        return request(url, auth=self._request_auth, config=self._request_config, data=data, headers=headers)

    def _get_by_url(self, url):
        """
        Does GET request by url and if successful, decodes it.

        :returns: dict
        """
        response = self._request(url)
        if response.status_code != 200:
            self._raise_error(response)
        return self._serializer.decode(response.content)

    def _get(self, resource_name=None, resource_id=None, **kwargs):
        """
        Does GET request by resource name and id and if successful, decodes it.

        :returns: dict
        """
        url = self._get_url(resource_name, resource_id, **kwargs)
        return self._get_by_url(url)

    def _raise_error(self, response):
        """
        Raises error.

        If request is not successful, calls this to raise error with possible description from response.

        :raises: BadHttpStatus
        """
        content = response.content
        try:
            data = self._serializer.decode(content)
            message = data.get('error_message', '')
        except ValueError:
            message = content
        message = '[%s] %s' % (response.status_code, message)
        raise BadHttpStatus(message, response=response)

    def __repr__(self):
        return '<Api: %s>' % self.parser.url
