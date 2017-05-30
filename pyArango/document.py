import json, types

from .theExceptions import (CreationError, DeletionError, UpdateError, ValidationError, SchemaViolation, InvalidDocument)

__all__ = ["Document", "Edge"]

class Store(object) :
    
    def __init__(self, collection, validators={}, initDct={}, patch=False) :
        self.store = {}
        self.patchStore = {}
        self.collection = collection
        self.validators = validators

        self.mustValidate = False
        for v in self.collection._validation.values() :
            if v :
                self.mustValidate = True
                break
        
        self.privates = set(["_to", "_from", "_id", "_key", "_rev"])
        self.subStores = {}
        self.set(initDct, patch)

    def resetPatch(self) :
        self.patchStore = {}

    def getPatches(self) :

        #inner dct are not trackable. Return the full store
        if not self.mustValidate :
            return self.getStore()

        res = {}
        res.update(self.patchStore)
        for k, v in self.subStores.items() :
            res[k] = v.getPatches()
        
        return res
        
    def getStore(self) :
        res = {}
        res.update(self.store)
        for k, v in self.subStores.items() :
            res[k] = v.getStore()
        
        return res

    def validateField(self, field) :
        if field not in self.validators and not self.collection._validation['allow_foreign_fields'] :
            raise SchemaViolation(self.collection.__class__, field)

        if field in self.validators :
            if self[field].__class__ is Store :
                return self[field].validate()
            
            if field in self.patchStore :
                return self.validators[field].validate(self.patchStore[field])
            else :
                return self.validators[field].validate(self.store[field])

        return True

    def validate(self) :
        if not self.mustValidate :
            return True

        res = {}
        for k in self.store.keys() :
            try :
                self.validateField(k)
                if self.store[k].__class__ is Store :
                    self.store[k].validate()
            except InvalidDocument as e :
                res.update(e.errors)
            except (ValidationError, SchemaViolation) as e:
                res[k] = str(e)

        if len(res) > 0 :
            raise InvalidDocument(res)
        
        return True

    def set(self, dct, patch) :
        if not self.mustValidate :
            self.store = dct
            self.patchStore = dct
            return

        for field, validator in self.validators.items() :
            if field in dct :
                if type(validator) is DictType or dct[field] is DictType :
                    if type(validator) is DictType and dct[field] is DictType :
                        self.store[field] = Store(self.collection, validators = self.validators[field], initDct = dct[field], patch = patch)
                        self.subStores[field] = self.store[field]
                    else :
                        raise SchemaViolation(self.collection.__class__, field)
                else :
                    self.store[field] = dct[field]
                    if patch :
                        self.patchStore[field] = self.store[field]
            else :
                if type(validator) is types.DictType :
                    self.store[field] = Store(self.collection, validators = self.validators[field], initDct = {})
                    self.subStores[field] = self.store[field]

    def __getitem__(self, k) :
        if self.collection._validation['allow_foreign_fields'] or self.collection.hasField(k) :
            return self.store.get(k)

        try :
            return self.store[k]
        except KeyError :
            raise SchemaViolation(self.collection.__class__, k)

    def __setitem__(self, field, value) :
        if not self.collection._validation['allow_foreign_fields'] and field not in self.validators :
            raise SchemaViolation(self.collection.__class__, field)
        
        if field in self.validators and type(self.validators[field]) is types.DictType :
            if type(value) is types.DictType :
                self.store[field] = Store(self.collection, validators = self.validators[field], initDct = value, patch=True)
            else :
                raise ValueError("dct not dct")
        else :
            self.store[field] = value
            self.patchStore[field] = self.store[field]

        if self.collection._validation['on_set'] :
            self.validateField(field)

    def __delitem__(self, k) :
        del(self.store[k])
        del(self.patchStore[k])

    def __contains__(self, k) :
        return k in self.store

    def __repr__(self) :
        return "<store: %s>" % repr(self.store)

class Document(object) :
    """The class that represents a document. Documents are meant to be instanciated by collections"""

    def __init__(self, collection, jsonFieldInit = {}) :
        self.reset(collection, jsonFieldInit)
        self.typeName = "ArangoDoc"

    def reset(self, collection, jsonFieldInit = {}) :
        """replaces the current values in the document by those in jsonFieldInit"""
        self.collection = collection
        self.connection = self.collection.connection
        self.documentsURL = self.collection.documentsURL

        # self._store = {}
        # self._patchStore = {}
        self._store = Store(self.collection, validators=self.collection._fields, initDct=jsonFieldInit)
        
        if self.collection._validation['on_load']:
            self._store.validate()

        self._id, self._rev, self._key = None, None, None
        self.URL = None

        
        self.modified = True

    def setPrivates(self, fieldDict) :
        """will set self._id, self._rev and self._key field. Private fields (starting by '_') are all accessed using the self. interface,
        other fields are accessed through self[fielName], the same as regular dictionnary in python"""
        try :
            self._id = fieldDict["_id"]
            self.URL = "%s/%s" % (self.documentsURL, self._id)
            del(fieldDict["_id"])

            self._rev = fieldDict["_rev"]
            del(fieldDict["_rev"])

            self._key = fieldDict["_key"]
            del(fieldDict["_key"])
        except KeyError :
            self._id, self._rev, self._key = None, None, None
            self.URL = None

    def set(self, fieldDict) :
        self._store.set(fieldDict)

    # def set(self, fieldDict = None, validate = True) :
    #     """Sets the document according to values contained in the dictinnary fieldDict. This will also set self._id/_rev/_key"""

    #     if fieldDict and self._id is None :
    #         self.setPrivates(fieldDict)

    #     if not validate :
    #         self.store.set(fieldDict)
    #         return

    #     if self.collection._validation['on_set']:
    #         for k in list(fieldDict.keys()) :
    #             self[k] = fieldDict[k]
    #     else :
    #         self._store.update(fieldDict)

    def save(self, waitForSync = False, **docArgs) :
        """Saves the document to the database by either performing a POST (for a new document) or a PUT (complete document overwrite).
        If you want to only update the modified fields use the .path() function.
        Use docArgs to put things such as 'waitForSync = True' (for a full list cf ArangoDB's doc).
        It will only trigger a saving of the document if it has been modified since the last save. If you want to force the saving you can use forceSave()"""

        if self.modified :

            params = dict(docArgs)
            params.update({'collection': self.collection.name, "waitForSync" : waitForSync })
            # payload = {}
            # payload.update(self._store)
            payload = self._store.getStore()
            if self.collection._validation['on_save'] :
                self._store.validate()
            if self.URL is None :
                if self._key is not None :
                    payload["_key"] = self._key
                payload = json.dumps(payload)
                r = self.connection.session.post(self.documentsURL, params = params, data = payload)
                update = False
            else :
                payload = json.dumps(payload)
                r = self.connection.session.put(self.URL, params = params, data = payload)
                update = True

            data = r.json()

            if (r.status_code == 201 or r.status_code == 202) and "error" not in data :
                if update :
                    self._rev = data['_rev']
                else :
                    self.setPrivates(data)
            else :
                if update :
                    raise UpdateError(data['errorMessage'], data)
                else :
                    raise CreationError(data['errorMessage'], data)

            self.modified = False

        self._store.resetPatch()
        # self._patchStore = {}

    def forceSave(self, **docArgs) :
        "saves even if the document has not been modified since the last save"
        self.modified = True
        self.save(**docArgs)

    def saveCopy(self) :
        "saves a copy of the object and become that copy. returns a tuple (old _key, new _key)"
        old_key = self._key
        self.reset(self.collection)
        self.save()
        return (old_key, self._key)

    def patch(self, keepNull = True, **docArgs) :
        """Saves the document by only updating the modified fields.
        The default behaviour concening the keepNull parameter is the opposite of ArangoDB's default, Null values won't be ignored
        Use docArgs for things such as waitForSync = True"""

        if self.URL is None :
            raise ValueError("Cannot patch a document that was not previously saved")

        payload = self._store.getPatches()
        
        if self.collection._validation['on_save'] :
            self.collection.validateDct(payload)

        if len(payload) > 0 :
            params = dict(docArgs)
            params.update({'collection': self.collection.name, 'keepNull' : keepNull})
            payload = json.dumps(payload)

            r = self.connection.session.patch(self.URL, params = params, data = payload)
            data = r.json()
            if (r.status_code == 201 or r.status_code == 202) and "error" not in data :
                self._rev = data['_rev']
            else :
                raise UpdateError(data['errorMessage'], data)

            self.modified = False

        self._store.resetPatch()
        # self._patchStore = {}

    def delete(self) :
        "deletes the document from the database"
        if self.URL is None :
            raise DeletionError("Can't delete a document that was not saved")
        r = self.connection.session.delete(self.URL)
        data = r.json()

        if (r.status_code != 200 and r.status_code != 202) or 'error' in data :
            raise DeletionError(data['errorMessage'], data)
        self.reset(self.collection)

        self.modified = True

    # def validate(self, patch = False) :
    #     "validates either the whole store, or only the patch store( patch = True) of the document according to the collection's settings.If logErrors returns a dictionary of errros per field, else raises exceptions"
    #     if patch :
    #         return self.collection.validateDct(self._patchStore)
    #     else :
    #         return self.collection.validateDct(self._store)

    def getInEdges(self, edges, rawResults = False) :
        "An alias for getEdges() that returns only the in Edges"
        return self.getEdges(edges, inEdges = True, outEdges = False, rawResults = rawResults)

    def getOutEdges(self, edges, rawResults = False) :
        "An alias for getEdges() that returns only the out Edges"
        return self.getEdges(edges, inEdges = False, outEdges = True, rawResults = rawResults)

    def getEdges(self, edges, inEdges = True, outEdges = True, rawResults = False) :
        """returns in, out, or both edges linked to self belonging the collection 'edges'.
        If rawResults a arango results will be return as fetched, if false, will return a liste of Edge objects"""
        try :
            return edges.getEdges(self, inEdges, outEdges, rawResults)
        except AttributeError :
            raise AttributeError("%s does not seem to be a valid Edges object" % edges)

    def __getitem__(self, k) :
        return self._store[k]


    # def __getitem__(self, k) :
    #     """Document fields are accessed in a dictionary like fashion: doc[fieldName]. With the exceptions of private fiels (starting with '_')
    #     that are accessed as object fields: doc._key"""
    #     if self.collection._validation['allow_foreign_fields'] or self.collection.hasField(k) :
    #         return self._store.get(k)

    #     try :
    #         return self._store[k]
    #     except KeyError :
    #         raise KeyError("Document of collection '%s' has no field '%s', for a permissive behaviour set 'allow_foreign_fields' to True" % (self.collection.name, k))

    def __setitem__(self, k, v) :
        self._store[k] = v

    # def __setitem__(self, k, v) :
    #     """Documents work just like dictionaries doc[fieldName] = value. With the exceptions of private fiels (starting with '_')
    #     that are accessed as object fields: doc._key"""

    #     def _recValidate(k, v) :
    #         if type(v) is dict :
    #             for kk, vv in v.items() :
    #                 newk = "%s.%s" % (k, kk)
    #                 _recValidate(newk, vv)
    #         else :
    #             self.collection.validateField(k, v)

    #     if self.collection._validation['on_set'] :
    #         _recValidate(k, v)

    #     self._store[k] = v
    #     if self.URL is not None :
    #         self._patchStore[k] = self._store[k]

    #     self.modified = True

    # def __delitem__(self, k) :
    #     del(self._store[k])

    def __str__(self) :
        return "%s '%s': %s" % (self.typeName, self._id, repr(self._store))

    def __repr__(self) :
        return "%s '%s': %s" % (self.typeName, self._id, repr(self._store))

class Edge(Document) :
    """An Edge document"""
    def __init__(self, edgeCollection, jsonFieldInit = {}) :
        self.reset(edgeCollection, jsonFieldInit)

    def reset(self, edgeCollection, jsonFieldInit = {}) :
        Document.reset(self, edgeCollection, jsonFieldInit)
        self.typeName = "ArangoEdge"

    def links(self, fromVertice, toVertice, **edgeArgs) :
        """
        An alias to save that updates the _from and _to attributes.
        fromVertice and toVertice, can be either strings or documents. It they are unsaved documents, they will be automatically saved.
        """

        if fromVertice.__class__ is Document :
            if not fromVertice._id :
                fromVertice._id.save()

            self["_from"] = fromVertice._id
        elif (type(fromVertice) is bytes) or (type(fromVertice) is str) :
            self["_from"] = fromVertice

        if toVertice.__class__ is Document :
            if not toVertice._id :
                toVertice._id.save()

            self["_to"] = toVertice._id
        elif (type(toVertice) is bytes) or (type(toVertice) is str) :
            self["_to"] = toVertice

        self.save(**edgeArgs)

    def save(self, **edgeArgs) :
        """Works like Document's except that you must specify '_from' and '_to' vertices before.
        There's also a links() function especially for first saves."""

        import types

        if "_from" not in self._store or "_to" not in self._store :
            raise AttributeError("You must specify '_from' and '_to' attributes before saving. You can also use the function 'links()'")

        Document.save(self, **edgeArgs)

    def __getattr__(self, k) :
        if k == "_from" or k == "_to" :
            return self._store[k]
        else :
            return Document.__getattr__(self, k)