import pprint
import urlparse
import urllib
from django.conf import settings
from django.utils import simplejson
import requests
import sys
from requests.auth import AuthBase
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
        self.api = api
        self._endpoint_url = endpoint_url
        self._schema_url = schema_url
        self._resource = filter(bool, endpoint_url.split('/'))[-1]

    def __repr__(self):
        return '<EndpointProxy %s>' % self.api.get_url(self._resource)

    def get_url(self):
        return '%s%s' % (self.api.base_url, self._endpoint_url)

    def one(self, id=None, **kw):
        return self.api.one(self._resource, id, **kw)

    def many(self, *ids, **kw):
        return self.api.many(self._resource, *ids, **kw)

    def find(self, **kw):
        return self.api.find(self._resource, **kw)

    def add(self, **kw):
        return self.api.add(self._resource, **kw)

    def update(self, id, **kw):
        return self.api.update(self._resource, id, **kw)

    def delete(self, id):
        return self.api.delete(self._resource, id)


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
        self.api = api
        self._type, id = self._service.get_resource_ident(self._url)
        self._id = int(id)
        self._resource = None

    def __repr__(self):
        if self._resource:
            return repr(self._resource)
        else:
            return '<ResourceProxy %s/%s>' % (self._type, self._id)

    def __getattr__(self, attr):
        return getattr(self.get(), attr)

    def __getitem__(self, item):
        return self.get()[item]

    def __contains__(self, attr):
        return attr in self.get()

    def get(self):
        """Load the resource

        Do nothing if already loaded.
        """
        if not self._resource:
            self._resource = self.api.one(self._type, self._id)
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


class ListProxy(ResourceListMixin):
    """
    List of connected ToMany items.

    Acts like a `list` but resolves ResourceProxy objects on access.

    E.g. api.user.one(1).mailings
    """
    def __init__(self, list, service, api):
        self._list = list
        self._service = service
        self.api = api

    def __repr__(self):
        return pprint.pformat(self._list)

    def _parse_item(self, item):
        if self._service.is_resource_url(item):
            return ResourceProxy(item, self._service, self.api)
        else:
            return item

    def _evaluate_item(self, item):
        item = self._parse_item(item)
        if isinstance(item, ResourceProxy):
            return item.get()
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
                resources = self.api.many(type, *ids)
                for id, resource in resources.items():
                    index = missing[type][int(id)]
                    items[index] = resource
            self._list[slice] = items
            return items
        item = self._evaluate_item(item)
        self._list[index] = item
        return item


class ResourceListProxy(object):

    def __init__(self, endpoint, meta, filters):
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(meta, dict)
        assert isinstance(filters, dict)
        self.endpoint = endpoint
        self._resources = []
        self._fetched = False
        self._meta = meta
        self._filters = filters

    def count(self):
        return self._meta['total_count']

    @property
    def resource_name(self):
        return self.endpoint.resource_name

    def _fetch_resources(self):
        self._fetched = True
        data = self.endpoint.api.get(self.resource_name, **self._filters)
        resources = Resource.manufacture_many(self.endpoint, data['objects'])
        self._resources += resources
        for item in resources:
            yield item
        new_next = data['meta']['next']
        for item in self._iterate_pages(new_next):
            yield item

    def _iterate_pages(self, next):
        if not next:
            return
        data = self.endpoint.api.get_by_relative_url(next)
        resources = Resource.manufacture_many(self.endpoint, data['objects'])
        self._resources += resources
        for item in resources:
            yield item
        new_next = data['meta']['next']
        for item in self._iterate_pages(new_next):
            yield item

    def __iter__(self):
        generator = self._resources if self._fetched else self._fetch_resources()
        for item in generator:
            yield item

    def __repr__(self):
        return '<%s %s, total count: %s>' % (
            self.__class__.__name__,
            self.resource_name,
            self.count()
        )


class Resource(object):
    """
    A fetched resource

    Its data available as properties.

    E.g. api.mailing.one(1)
    """
    def __init__(self, endpoint, data, name, id, url):
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(data, dict)
        assert isinstance(name, basestring)
        assert isinstance(id, basestring)
        assert isinstance(url, basestring)
        self.endpoint = endpoint
        self._data = data
        self._name = name
        self._id = id
        self._url = url

    def __repr__(self):
        return '<%s %s: %s>' % (
            self.__class__.__name__,
            self._url,
            self._data
        )

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
    def manufacture(cls, endpoint, data):
        """
        Manufactures Resource from raw data returned by server.

        Replace related resource URLs with ResourceProxy objects.

        :param: endpoint: EndpointProxy
        :param: data: dict
        :returns: Resource
        """
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(data, dict)
        url = data['resource_uri']
        del data['resource_uri']

        for attr, value in data.items():
            if endpoint.api.parser.is_resource_url(value):
                data[attr] = ResourceProxy(value, endpoint.api.parser, endpoint.api)
            elif isinstance(value, list):
                data[attr] = ListProxy(value, endpoint.api.parser, endpoint.api)

        resource_name, resource_id = endpoint.api.parser.get_resource_ident(url)
        return Resource(endpoint, data, resource_name, resource_id, url)

    @classmethod
    def manufacture_many(cls, endpoint, data):
        """
        Manufactures Resources from raw data list.

        :param: endpoint: EndpointProxy
        :param: data: iterable
        :returns: list(Resource)
        """
        assert isinstance(endpoint, EndpointProxy)
        assert hasattr(data, '__iter__')
        return [Resource.manufacture(endpoint, item) for item in data]


class EndpointProxy(object):
    """
    Proxy object to a service endpoint

    E.g. api.mailing
    """
    def __init__(self, api, endpoint_url, schema_url):
        assert isinstance(api, Api)
        assert isinstance(endpoint_url, basestring)
        assert isinstance(schema_url, basestring)
        self.api = api
        self._endpoint_url = endpoint_url
        self._schema_url = schema_url
        self.resource_name = filter(bool, endpoint_url.split('/'))[-1]

    def __repr__(self):
        return '<%s %s>' % (
            self.__class__.__name__,
            self.get_url(),
        )

    def get_url(self):
        return self.api.get_url(self.resource_name)

    def get_schema_url(self):
        return '%s%s' % (self.api.parser.base_url, self._schema_url)

    def get_schema(self):
        """
        Returns endpoint schema.

        :returns: dict
        """
        return self.api.get_by_absolute_url(self.get_schema_url())

    def get(self, id=None, **kwargs):
        """
        Returns one resource.

        Specified by id or by django QuerySet filter attributes.

        :returns: Resource
        """
        if id:
            data = self.api.get(self.resource_name, id, **kwargs)
            return Resource.manufacture(self, data)
        if not kwargs:
            raise ResourceIdMissing
        list_proxy = self.filter(**kwargs)
        if list_proxy.count() != 1:
            raise TooManyResources
        return [item for item in list_proxy][0]

    def get_many(self, *ids, **kwargs):
        """
        Returns more resources.

        Specified by ids.
        This doesn't have paging so you get all the records you want.

        :returns: dict(str(resource id): Resource)
        """
        id = 'set/' + ';'.join(map(str, ids))
        data = self.api.get(self.resource_name, id)
        resources = Resource.manufacture_many(self, data['objects'])
        # Transform a list of Resource in a dict using resource ID as key
        resources = dict([(r.id, r) for r in resources])
        # Add not found IDs to the dict
        if 'not_found' in data:
            for id in data['not_found']:
                resources[int(id)] = None
        return resources

    def all(self):
        """
        Returns all resources generator.

        If you want to walk trough ALL resources. Use self.__iter__()

        :returns: SearchResponse
        """
        return self.filter()

    def filter(self, **kwargs):
        """
        Returns filtered resources generator.

        Specified by django QuerySet filter attributes.

        :returns: SearchResponse
        """
        data = self.api.get(self.resource_name, **kwargs)
        meta = data['meta']
        return ResourceListProxy(self, meta, kwargs)

    def add(self, **kwargs):
        """
        Adds resource to this endpoint and returns it.

        Issues POST to endpoint url with resource parameters given as **kwargs.

        :returns: Resource
        """
        url = self.get_url()
        headers = {'content-type': 'application/json'}
        response = self.api.request(url, request=requests.post, data=simplejson.dumps(kwargs), headers=headers)
        if response.status_code != 201:
            self.api.raise_error(response)
        data = self.api.get_by_absolute_url(response.headers['location'])
        return Resource.manufacture(self, data)


class Parser(object):
    """
    Service url parser.
    """
    def __init__(self, url):
        assert isinstance(url, basestring)
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
        assert isinstance(service_url, basestring)
        assert isinstance(auth, (tuple, AuthBase))
        assert isinstance(config, dict)
        self._request_auth = auth
        self._request_config = config
        if settings.DEBUG:
            self._request_config['verbose'] = sys.stdout
        self.parser = Parser(service_url)
        self._serializer = serializer or JsonSerializer()
        assert isinstance(self._serializer, SerializerInterface)
        self._endpoints = self.get() # The API endpoint should return resource endpoints list.

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

    def get_url(self, resource_name=None, resource_id=None, **kwargs):
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

    def request(self, url, request=requests.get, data=None, headers=None, **kwargs):
        """
        Does the request.

        :returns: requests.models.Response
        """
        return request(url, auth=self._request_auth, config=self._request_config, data=data, headers=headers)

    def get_by_absolute_url(self, url):
        """
        Does GET request by url and if successful, decodes it.

        :returns: dict
        """
        assert isinstance(url, basestring)
        response = self.request(url)
        if response.status_code != 200:
            self.raise_error(response)
        return self._serializer.decode(response.content)

    def get_by_relative_url(self, url):
        """
        Does GET request by relative url and if successful, decodes it.

        :returns: dict
        """
        assert isinstance(url, basestring)
        url = '%s%s' % (self.parser.base_url, url)
        return self.get_by_absolute_url(url)

    def get(self, resource_name=None, resource_id=None, **kwargs):
        """
        Does GET request by resource name and id and if successful, decodes it.

        :returns: dict
        """
        url = self.get_url(resource_name, resource_id, **kwargs)
        return self.get_by_absolute_url(url)

    def raise_error(self, response):
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
        return '<%s: %s>' % (
            self.__class__.__name__,
            self.parser.url
        )
