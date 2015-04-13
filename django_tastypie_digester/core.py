from logging import getLogger
from math import ceil


import sys
if sys.version_info.major == 3:
    from urllib.parse import urlsplit

    native_string_bases = (str, bytes)
else:
    from urlparse import urlsplit

    native_string_bases = (basestring,)


import urllib
import json
import requests
from requests.auth import AuthBase
from .serializers import JsonSerializer, SerializerInterface, JsonLazyEncoder
from .exceptions import BadHttpStatus, ResourceIdMissing, TooManyResources,\
    ResourceDeleted

logger = getLogger(__name__)


class ResourceProxy(object):
    """
    Proxy to not evaluated resource.
    (like resource property pointing to another resource)

    It lazily fetches data.

    E.g. api.mailing.get(1).user

    :param: endpoint: EndpointProxy
    :param: id: native_string_bases
    """
    def __init__(self, endpoint, id):
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(id, native_string_bases)
        self._endpoint = endpoint
        self._id = id
        self._resource = None

    def __repr__(self):
        if self._resource:
            return repr(self._resource)
        else:
            return '<%s %s/%s>' % (
                self.__class__.__name__,
                self._endpoint.resource_name,
                self._id
            )

    def __getattr__(self, attr):
        """
        Returns resource property.

        E.g. api.mailing.get(1).user.username

        :raises: AttributeError
        :returns: mixed
        """
        return getattr(self._fetch(), attr)

    def _fetch(self):
        """
        Returns, possibly fetches resource.
        """
        if not self._resource:
            self._resource = self._endpoint.get(self._id)
        return self._resource

    @classmethod
    def manufacture(cls, api, url):
        """
        Manufactures ResourceProxy object.

        :param: api: Api
        :param: url: native_string_bases
        :returns: ResourceProxy
        """
        assert isinstance(api, Api)
        assert isinstance(url, native_string_bases)
        name, id = api.parser.get_resource_ident(url)
        endpoint = api.get_endpoint(name)
        return ResourceProxy(endpoint, id)


class ResourceProxyList(object):
    """
    List of ResourceProxy.

    Evaluated lazily.

    E.g. api.user.get(1).mailings

    :const: PAGE_ROWS: How many records should fetch on one page on evaluation.
    :param: endpoint: EndpointProxy
    :param: ids: list
    """
    PAGE_ROWS = 20

    def __init__(self, endpoint, ids):
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(ids, list)
        self._endpoint = endpoint
        self._ids = ids
        self._cache = {}
        self._is_cached = False

    def __iter__(self):
        """
        Iterates resources.

        Fetches them if not fetched before. Fetches them page after page.

        :yields: Resource
        """
        generator = self._cache.itervalues() if self._is_cached else self._fetch()
        for item in generator:
            yield item

    def __getitem__(self, item):
        """
        Returns single resource.

        For getting more resources, use iteration.

        :returns: Resource
        """
        item = str(item)
        if item not in self._ids:
            raise KeyError(item)
        if item not in self._cache:
            self._cache[item] = self._endpoint.get(item)
        return self._cache[item]

    def __repr__(self):
        return '<%s %s, total count: %s>' % (
            self.__class__.__name__,
            self._endpoint.resource_name,
            len(self._ids)
            )

    def _fetch(self):
        """
        Fetches resources page by page.

        :yields: Resource
        """
        self._is_cached = True
        pages_count = int(ceil(len(self._ids) / self.PAGE_ROWS))
        for page_num in range(pages_count):
            offset = page_num * self.PAGE_ROWS
            onset = offset + self.PAGE_ROWS
            ids_slice = self._ids[offset:onset]
            for key, val in self._endpoint.get_many(*ids_slice).iteritems():
                self._cache[key] = val
                yield val

    @classmethod
    def manufacture(cls, api, data):
        """
        Factory for ResourceProxyList.

        :param: api: Api
        :param: data: list
        :returns: ResourceProxyList
        """
        assert isinstance(api, Api)
        assert isinstance(data, list)
        if not data:
            return []
        ids = []
        for item in data:
            name, id = api.parser.get_resource_ident(item)
            ids.append(id)
        endpoint = api.get_endpoint(name)
        return ResourceProxyList(endpoint, ids)


class ResourceList(object):
    """
    Resource list.

    Endpoint helper for paginated lists.

    E.g.
        api.mailing.all()
        api.mailing.filter(...)

    :param: endpoint: EndpointProxy
    :param: meta: dict
    :param: filters: dict
    """
    def __init__(self, endpoint, meta, filters):
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(meta, dict)
        assert isinstance(filters, dict)
        self.endpoint = endpoint
        self._cache = []
        self._is_cached = False
        self._meta = meta
        self._filters = filters

    def count(self):
        """
        Returns total items count.

        :returns: int
        """
        return int(self._meta['total_count'])

    @property
    def resource_name(self):
        return self.endpoint.resource_name

    def _fetch(self):
        """
        Used by iteration. Fetches all resources page by page.

        :yields: Resource
        """
        self._is_cached = True
        data = self.endpoint.api.get(self.resource_name, **self._filters)
        resources = Resource.manufacture_many(self.endpoint, data['objects'])
        self._cache += resources
        for item in resources:
            yield item
        new_next = data['meta']['next']
        for item in self._iterate_pages(new_next):
            yield item

    def _iterate_pages(self, next):
        """
        Iterates pages.

        :yields: Resource
        """
        if not next:
            return
        data = self.endpoint.api.get_by_relative_url(next)
        resources = Resource.manufacture_many(self.endpoint, data['objects'])
        self._cache += resources
        for item in resources:
            yield item
        new_next = data['meta']['next']
        for item in self._iterate_pages(new_next):
            yield item

    def __iter__(self):
        """
        Iterates all the resources belonged resources.

        Fetches them if not fetched before. Fetches them page after page.

        :yields: Resource
        """
        generator = self._cache if self._is_cached else self._fetch()
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
    Resource

    Its data are available as properties.

    E.g. api.mailing.get(1)

    :param: endpoint: EndpointProxy
    :param: data: dict
    :param: id: native_string_bases
    """
    def __init__(self, endpoint, data, id):
        assert isinstance(endpoint, EndpointProxy)
        assert isinstance(data, dict)
        assert isinstance(id, native_string_bases)
        self.endpoint = endpoint
        self._data = data
        self._id = id
        self._is_deleted = False

    @property
    def name(self):
        return self.endpoint.resource_name

    def get_url(self):
        return self.endpoint.api.get_url(self.name, self._id)

    def __repr__(self):
        return '<%s %s/%s: %s>' % (
            self.__class__.__name__,
            self.name,
            self._id,
            self._data
        )

    def __getattr__(self, attr):
        """
        Returns resource property.

        E.g. api.mailing.get(1).email

        :raises: AttributeError
        :returns: mixed
        """
        if attr in self._data:
            return self._data[attr]
        else:
            raise AttributeError(attr)

    def update(self, **kwargs):
        """
        Updates resources by PATCH request and returns updated resource.

        :keyword params: resource fields
        :raises: BadHttpStatus if returned status is not 202
        :returns: Resource
        """
        if self._is_deleted:
            raise ResourceDeleted
        url = self.get_url()
        headers = {'content-type': 'application/json'}
        response = self.endpoint.api.request(url, request=requests.patch, data=json.dumps(kwargs, cls=JsonLazyEncoder), headers=headers)
        logger.debug('Patching data: %s' % kwargs)
        if response.status_code != 202:
            self.endpoint.api.raise_error(response)
        return self.endpoint.get(self._id)

    def delete(self):
        """
        Deletes resource by DELETE request.

        :raises: BadHttpStatus if returned status is not 204
        :returns: True
        """
        if self._is_deleted:
            raise ResourceDeleted
        url = self.get_url()
        response = self.endpoint.api.request(url, request=requests.delete)
        if response.status_code != 204:
            self.endpoint.api.raise_error(response)
        self._is_deleted = True
        return True

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
                data[attr] = ResourceProxy.manufacture(endpoint.api, value)
            elif isinstance(value, list):
                data[attr] = ResourceProxyList.manufacture(endpoint.api, value)
        resource_name, resource_id = endpoint.api.parser.get_resource_ident(url)
        return Resource(endpoint, data, resource_id)

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
    Proxy to resource endpoint

    E.g. api.mailing

    :param: api: Api
    :param: endpoint_url: native_string_bases
    :param: schema_url: native_string_bases
    """
    def __init__(self, api, endpoint_url, schema_url):
        assert isinstance(api, Api)
        assert isinstance(endpoint_url, native_string_bases)
        assert isinstance(schema_url, native_string_bases)
        self.api = api
        self._endpoint_url = endpoint_url
        self._schema_url = schema_url
        resource_name_iter = filter(bool, endpoint_url.split('/'))
        self.resource_name = list(resource_name_iter)[-1]

    def __repr__(self):
        return '<%s %s>' % (
            self.__class__.__name__,
            self.get_url(),
        )

    def get_url(self):
        """
        Returns endpoint url.

        :returns: str
        """
        return self.api.get_url(self.resource_name)

    def get_schema_url(self):
        """
        Returns endpoint schema url.

        :returns: str
        """
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

        :raises: BadHttpStatus if returned status is not 200
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

        :raises: BadHttpStatus if returned status is not 200
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

        :raises: BadHttpStatus if returned status is not 200
        :returns: SearchResponse
        """
        return self.filter()

    def filter(self, **kwargs):
        """
        Returns filtered resources generator.

        Specified by django QuerySet filter attributes.

        :raises: BadHttpStatus if returned status is not 200
        :returns: SearchResponse
        """
        data = self.api.get(self.resource_name, **kwargs)
        meta = data['meta']
        return ResourceList(self, meta, kwargs)

    def add(self, **kwargs):
        """
        Adds resource to this endpoint and returns it.

        Issues POST to endpoint url with resource parameters given as **kwargs.

        :raises: BadHttpStatus if returned status is not 201
        :returns: Resource
        """
        url = self.get_url()
        headers = {'content-type': 'application/json'}
        response = self.api.request(url, request=requests.post, data=json.dumps(kwargs, cls=JsonLazyEncoder), headers=headers)
        logger.debug('Posting data: %s' % kwargs)
        if response.status_code != 201:
            self.api.raise_error(response)
        data = self.api.get_by_absolute_url(response.headers['location'])
        return Resource.manufacture(self, data)


class Parser(object):
    """
    Service url parser.

    :param: url: native_string_bases
    """
    def __init__(self, url):
        assert isinstance(url, native_string_bases)
        self.url = url
        self.base_url, self.base_path = self._get_url_parts(url)

    def _get_url_parts(self, url):
        """
        Extracts the base URL and the base path from the service URL.

        E.g.
        self._get_url_parts('http://foo.bar/1/') -> ('http://foo.bar', '/1/')

        :returns: 2-tuple (str, str)
        """
        assert isinstance(url, native_string_bases)
        proto, host, path = urlsplit(url)[0:3]
        return '%s://%s' % (proto, host), path

    def is_resource_url(self, url):
        """
        Returns True if `url` is a valid resource URL

        :returns: bool
        """
        return isinstance(url, native_string_bases) and url.startswith(self.base_path)

    def get_resource_ident(self, url):
        """
        Parses a resource URL and returns a tuple of (resource_name, resource_id)

        E.g.
        self.get_resource_ident('http://foo.bar/1/resource/1') -> ('resource', '1')

        :returns: 2-tuple (str, str)
        """
        assert isinstance(url, native_string_bases)
        return url.split('/')[-3:-1]


class _Logger():
    """
    Custom logger for requests.
    """
    def write(self, *args, **kwargs):
        logger.debug(*args, **kwargs)


class Api(object):
    """
    The TastyPie client.
    Supposed to be REST api client, but uses some advantages of TastyPie which other REST apis do not implement.

    E.g. api = Api('http://127.0.0.1:8000/api/v1/', auth=('martin', '***'))
    """

    def __init__(self, service_url, serializer=None, auth=None, config={}, debug=False, load_endpoints=True, strip_trailing_slash=False, **kwargs):
        """
        :param service_url: native_string_bases
        :param serializer: None|SerializerInterface
        :param auth: tuple|AuthBase
        :param config: dict                              DEPRECATED
            config dict directly passed to requests
        :param debug: bool                               DEPRECATED
        :param load_endpoints: bool
            whether to load endpoints (TastyPie feature) on initialization
        :param strip_trailing_slash: bool
            whether to strip trailing slashes in urls, e.g.
                http://127.0.0.1:8000/api/v1/mailings/ vs.
                http://127.0.0.1:8000/api/v1/mailings
            TastyPie api supports trailing slashes (django uses interface for redirecting from "non-slashed" url to "slashed")
            Other clients does not have to support trailing slashes
        :param kwargs: **dict
            kwargs directly passed to requests
        """
        assert isinstance(service_url, native_string_bases)
        assert isinstance(auth, (tuple, AuthBase))
        self._request_auth = auth
        self._request_kwargs = kwargs
        self.parser = Parser(service_url)
        self._serializer = serializer or JsonSerializer()
        self._strip_trailing_slash = strip_trailing_slash
        assert isinstance(self._serializer, SerializerInterface)
        if load_endpoints:
            # The API endpoint should return resource endpoints list.
            self._endpoints = self.get()

    def __getattr__(self, name):
        """
        Summons endpoints.

        E.g. api.mailing

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
        assert isinstance(name, native_string_bases)
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
        if self._strip_trailing_slash:
            url = url.strip('/')
        if kwargs:
            params = []
            for key, value in kwargs.items():
                if not isinstance(value, (tuple, list)):
                    value = [value]
                for value_item in value:
                    if isinstance(value_item, native_string_bases):
                        params.append((key, value_item.encode('utf-8')))
                    else:
                        params.append((key, value_item))
            url += '?' + urllib.urlencode(params)
        return url

    def request(self, url, request=requests.get, data=None, headers=None):
        """
        Does the request.

        :returns: requests.models.Response
        """
        return request(url, auth=self._request_auth, data=data, headers=headers, **self._request_kwargs)

    def get_by_absolute_url(self, url):
        """
        Does GET request by url and if successful, decodes it.

        :returns: dict
        """
        assert isinstance(url, native_string_bases)
        response = self.request(url)
        if response.status_code != 200:
            self.raise_error(response)
        response.enconding = 'utf8'
        return self._serializer.decode(response.text)

    def get_by_relative_url(self, url):
        """
        Does GET request by relative url and if successful, decodes it.

        :returns: dict
        """
        assert isinstance(url, native_string_bases)
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
        assert isinstance(response, requests.models.Response)
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
