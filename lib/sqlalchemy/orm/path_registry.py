# orm/path_registry.py
# Copyright (C) 2005-2013 the SQLAlchemy authors and contributors <see AUTHORS file>
#
# This module is part of SQLAlchemy and is released under
# the MIT License: http://www.opensource.org/licenses/mit-license.php
"""Path tracking utilities, representing mapper graph traversals.

"""

from .. import inspection
from .. import util
from itertools import chain
from .base import class_mapper

def _unreduce_path(path):
    return PathRegistry.deserialize(path)

class PathRegistry(object):
    """Represent query load paths and registry functions.

    Basically represents structures like:

    (<User mapper>, "orders", <Order mapper>, "items", <Item mapper>)

    These structures are generated by things like
    query options (joinedload(), subqueryload(), etc.) and are
    used to compose keys stored in the query._attributes dictionary
    for various options.

    They are then re-composed at query compile/result row time as
    the query is formed and as rows are fetched, where they again
    serve to compose keys to look up options in the context.attributes
    dictionary, which is copied from query._attributes.

    The path structure has a limited amount of caching, where each
    "root" ultimately pulls from a fixed registry associated with
    the first mapper, that also contains elements for each of its
    property keys.  However paths longer than two elements, which
    are the exception rather than the rule, are generated on an
    as-needed basis.

    """

    def __eq__(self, other):
        return other is not None and \
            self.path == other.path

    def set(self, attributes, key, value):
        attributes[(key, self.path)] = value

    def setdefault(self, attributes, key, value):
        attributes.setdefault((key, self.path), value)

    def get(self, attributes, key, value=None):
        key = (key, self.path)
        if key in attributes:
            return attributes[key]
        else:
            return value

    def __len__(self):
        return len(self.path)

    @property
    def length(self):
        return len(self.path)

    def pairs(self):
        path = self.path
        for i in range(0, len(path), 2):
            yield path[i], path[i + 1]

    def contains_mapper(self, mapper):
        for path_mapper in [
            self.path[i] for i in range(0, len(self.path), 2)
        ]:
            if path_mapper.is_mapper and \
                path_mapper.isa(mapper):
                return True
        else:
            return False

    def contains(self, attributes, key):
        return (key, self.path) in attributes

    def __reduce__(self):
        return _unreduce_path, (self.serialize(), )

    def serialize(self):
        path = self.path
        return list(zip(
            [m.class_ for m in [path[i] for i in range(0, len(path), 2)]],
            [path[i].key for i in range(1, len(path), 2)] + [None]
        ))

    @classmethod
    def deserialize(cls, path):
        if path is None:
            return None

        p = tuple(chain(*[(class_mapper(mcls),
                            class_mapper(mcls).attrs[key]
                                if key is not None else None)
                            for mcls, key in path]))
        if p and p[-1] is None:
            p = p[0:-1]
        return cls.coerce(p)

    @classmethod
    def per_mapper(cls, mapper):
        return EntityRegistry(
                cls.root, mapper
            )

    @classmethod
    def coerce(cls, raw):
        return util.reduce(lambda prev, next: prev[next], raw, cls.root)

    def token(self, token):
        return TokenRegistry(self, token)

    def __add__(self, other):
        return util.reduce(
                    lambda prev, next: prev[next],
                    other.path, self)

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self.path, )


class RootRegistry(PathRegistry):
    """Root registry, defers to mappers so that
    paths are maintained per-root-mapper.

    """
    path = ()
    has_entity = False
    def __getitem__(self, entity):
        return entity._path_registry
PathRegistry.root = RootRegistry()

class TokenRegistry(PathRegistry):
    def __init__(self, parent, token):
        self.token = token
        self.parent = parent
        self.path = parent.path + (token,)

    has_entity = False

    def __getitem__(self, entity):
        raise NotImplementedError()


class PropRegistry(PathRegistry):
    def __init__(self, parent, prop):
        # restate this path in terms of the
        # given MapperProperty's parent.
        insp = inspection.inspect(parent[-1])
        if not insp.is_aliased_class or insp._use_mapper_path:
            parent = parent.parent[prop.parent]
        elif insp.is_aliased_class and insp.with_polymorphic_mappers:
            if prop.parent is not insp.mapper and \
                prop.parent in insp.with_polymorphic_mappers:
                subclass_entity = parent[-1]._entity_for_mapper(prop.parent)
                parent = parent.parent[subclass_entity]

        self.prop = prop
        self.parent = parent
        self.path = parent.path + (prop,)

    @util.memoized_property
    def has_entity(self):
        return hasattr(self.prop, "mapper")

    @util.memoized_property
    def entity(self):
        return self.prop.mapper

    @property
    def mapper(self):
        return self.entity

    @property
    def entity_path(self):
        return self[self.entity]

    def __getitem__(self, entity):
        if isinstance(entity, (int, slice)):
            return self.path[entity]
        else:
            return EntityRegistry(
                self, entity
            )

class EntityRegistry(PathRegistry, dict):
    is_aliased_class = False
    has_entity = True

    def __init__(self, parent, entity):
        self.key = entity
        self.parent = parent
        self.is_aliased_class = entity.is_aliased_class
        self.entity = entity
        self.path = parent.path + (entity,)
        self.entity_path = self

    @property
    def mapper(self):
        return inspection.inspect(self.entity).mapper

    def __bool__(self):
        return True
    __nonzero__ = __bool__

    def __getitem__(self, entity):
        if isinstance(entity, (int, slice)):
            return self.path[entity]
        else:
            return dict.__getitem__(self, entity)

    def __missing__(self, key):
        self[key] = item = PropRegistry(self, key)
        return item


