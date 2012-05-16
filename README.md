Tastypie digest
===============


This package is built on tastypie-client
----------------------------------------

Author: Philippe Muller
Package: http://pypi.python.org/pypi/tastypie-client
Docs: http://packages.python.org/tastypie-client


Requires
--------

* requests >= 0.11.2


Usage
-----

### Client initialization

```
In [1]: from django_tastypie_digester import Api
In [2]: api = Api('http://127.0.0.1:8000/api/v1/')
In [3]: api
Out[3]: <Api: http://127.0.0.1:8000/api/v1/>
```

Note: All unsuccessful communication with api server raises BadHttpStatus exception. It is discussed later.

### Endpoints listing and getting

```
In [3]: api.get_endpoints()
Out[3]: {u'mailing': <EndpointProxy http://127.0.0.1:8000/api/v1/mailing/>}
```

```
In [4]: api.get_endpoint('mailing')
Out[4]: <EndpointProxy http://127.0.0.1:8000/api/v1/mailing/>

In [5]: api.mailing
Out[5]: <EndpointProxy http://127.0.0.1:8000/api/v1/mailing/>
```

### Getting resources

To get all the recources in endpoint, do:

```
In [6]: api.mailing.all()
Out[6]: <ResourceList mailing, total count: 130>
```

ResourceList object is ready only for iteration. Resources are fetched page by page lazyly.

To get one resource of known id, do:

```
In [7]: api.mailing.get(12)
Out[7]: <Resource mailing/12: {u'customer': u'30509', ... u'email': u'laserdisc35@hotmail.com'}>
```

To get more resources of known ids at once, do:

```
In [9]: api.mailing.get_many(11, 23)
Out[9]:
{
    u'11': <Resource mailing/11: {u'customer': u'30509', ...}>,
    u'23': <Resource mailing/23: {u'customer': u'65997', ...}>
}
```

To get a filtered list of resources, do:

```
In [11]: api.mailing.filter(kind__contains='cms')
Out[11]: <ResourceList mailing, total count: 14>
```

Filters and their types have to be configurated on the api side.
ResourceList object is ready only for iteration. Resources are fetched page by page lazyly.

### Adding resources

Realized by POST request to endpoint url. Data are passed as keyword arguments. Returns the newly created resource.

```
In [14]: api.mailing.add(customer=u'00936175', ...)
Out[14]: <Resource mailing/131: {u'customer': u'00936175', ...}>
```

### Updating resources

Calls PATCH request to resource url. Data are passed as keyword arguments. Returns the updated resource.

```
In [15]: api.mailing.get(1)
Out[15]: <Resource mailing/1: {u'customer': u'30509', ...}>

In [16]: api.mailing.get(1).update(customer='999')
Out[16]: <Resource mailing/1: {u'customer': u'999', ...}>
```

### Deleting resources

Calls DELETE request to resource url. Returns True.

```
In [20]: api.mailing.get(131).delete()
Out[20]: True
```

### Raised exceptions

All unsuccessful communication with api server raises BadHttpStatus exception.

E.g. if DELETE requests are not allowed on the api side the delete call will endup with 405 METHOD NOT ALLOWED status:

```
In [19]: api.mailing.get(131).delete()
---------------------------------------------------------------------------
BadHttpStatus                             Traceback (most recent call last)
/home/martin/devel/solweb/<ipython-input-19-7a74508b3864> in <module>()
----> 1 api.mailing.get(131).delete()

/home/martin/devel/solweb/qdqlibs/django_tastypie_digester/core.py in delete(self)
    339         response = self.endpoint.api.request(url, request=requests.delete)
    340         if response.status_code != 204:
--> 341             self.endpoint.api.raise_error(response)
    342         self._is_deleted = True
    343         return True

/home/martin/devel/solweb/qdqlibs/django_tastypie_digester/core.py in raise_error(self, response)
    707             message = content
    708         message = '[%s] %s' % (response.status_code, message)
--> 709         raise BadHttpStatus(message, response=response)
    710
    711     def __repr__(self):

BadHttpStatus: [405]
```

BadHttpStatus includes the unsuccessful response object

```
BadHttpStatus().response # instance of requests.models.Response
```

And response status code (proxied from response)

```
BadHttpStatus().status # 405
BadHttpStatus().response.status_copde # 405
```


### Traversing to related resources

Proxies to related resources

```
In [29]: api.mailing.get(1).user
Out[29]: <ResourceProxy user/1>
```

Proxies to related lists

```
In [30]: api.user.get(1).mailings
Out[30]: <ResourceProxyList mailing, total count: 116>
```

### Endpoint schema

```
In [31]: api.user.get_schema()
Out[31]:
{
    u'allowed_detail_http_methods': [u'get', u'post'],
    u'allowed_list_http_methods': [u'get', u'post'],
    u'default_format': u'application/json',
    u'default_limit': 20,
    u'fields': {
        u'email': {u'blank': False,
        u'default': u'',
        u'help_text': u'Unicode string data. Ex: "Hello World"',
        u'nullable': False,
        u'readonly': False,
        u'type': u'string',
        u'unique': False},
        ...
    }
}

```