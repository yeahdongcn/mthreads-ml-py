##
# Python bindings for the MTML library
##
import string
import sys
import threading
from ctypes import *
from functools import wraps

## C Type mappings ##
## Constants
MTML_DEVICE_UUID_BUFFER_SIZE = 48

## Enums
_mtmlReturn_t = c_uint
MTML_SUCCESS = 0
MTML_ERROR_NOT_SUPPORTED = 4
MTML_ERROR_UNINITIALIZED = 666
MTML_ERROR_FUNCTION_NOT_FOUND = 667
MTML_ERROR_UNKNOWN = 999

_mtmlMtLinkState_t = c_uint
MTML_MTLINK_STATE_DOWN = 0
MTML_MTLINK_STATE_UP = 1
MTML_MTLINK_STATE_DOWNGRADE = 2


## Library structures
class struct_c_mtmlLibrary_t(Structure):
    pass  # opaque handle


c_mtmlLibrary_t = POINTER(struct_c_mtmlLibrary_t)


## Device structures
class struct_c_mtmlDevice_t(Structure):
    pass  # opaque handle


c_mtmlDevice_t = POINTER(struct_c_mtmlDevice_t)


class _PrintableStructure(Structure):
    """
    Abstract class that produces nicer __str__ output than ctypes.Structure.
    e.g. instead of:
      >>> print str(obj)
      <class_name object at 0x7fdf82fef9e0>
    this class will print
      class_name(field_name: formatted_value, field_name: formatted_value)

    _fmt_ dictionary of <str _field_ name> -> <str format>
    e.g. class that has _field_ 'hex_value', c_uint could be formatted with
      _fmt_ = {"hex_value" : "%08X"}
    to produce nicer output.
    Default formatting string for all fields can be set with key "<default>" like:
      _fmt_ = {"<default>" : "%d MHz"} # e.g all values are numbers in MHz.
    If not set it's assumed to be just "%s"

    Exact format of returned str from this class is subject to change in the future.
    """

    _fmt_ = {}

    def __str__(self):
        result = []
        for x in self._fields_:
            key = x[0]
            value = getattr(self, key)
            fmt = "%s"
            if key in self._fmt_:
                fmt = self._fmt_[key]
            elif "<default>" in self._fmt_:
                fmt = self._fmt_["<default>"]
            result.append(("%s: " + fmt) % (key, value))
        return self.__class__.__name__ + "(" + ", ".join(result) + ")"

    def __getattribute__(self, name):
        res = super(_PrintableStructure, self).__getattribute__(name)
        # need to convert bytes to unicode for python3 don't need to for python2
        # Python 2 strings are of both str and bytes
        # Python 3 strings are not of type bytes
        # ctypes should convert everything to the correct values otherwise
        if isinstance(res, bytes):
            if isinstance(res, str):
                return res
            return res.decode()
        return res

    def __setattr__(self, name, value):
        if isinstance(value, str):
            # encoding a python2 string returns the same value, since python2 strings are bytes already
            # bytes passed in python3 will be ignored.
            value = value.encode()
        super(_PrintableStructure, self).__setattr__(name, value)


## MtLink structures
class c_mtmlMtLinkSpec_t(_PrintableStructure):
    _fields_ = [
        ("version", c_uint),
        ("bandWidth", c_uint),
        ("linkNum", c_uint),
        ("rsvd", c_uint * 4),
    ]


## Lib loading ##
mtmlLib = None
libLoadLock = threading.Lock()
libHandle = c_mtmlLibrary_t()
_mtmlLib_refcount = 0  # Incremented on each mtmlInit and decremented on mtmlShutdown


## Error Checking ##
class MTMLError(Exception):
    _valClassMapping = dict()
    # List of currently known error codes
    _errcode_to_string = {
        MTML_ERROR_UNINITIALIZED: "Uninitialized",
        MTML_ERROR_NOT_SUPPORTED: "Not Supported",
        MTML_ERROR_FUNCTION_NOT_FOUND: "Function Not Found",
        MTML_ERROR_UNKNOWN: "Unknown Error",
    }

    def __new__(typ, value):
        """
        Maps value to a proper subclass of MTMLError.
        See _extractMTMLErrorsAsClasses function for more details
        """
        if typ == MTMLError:
            typ = MTMLError._valClassMapping.get(value, typ)
        obj = Exception.__new__(typ)
        obj.value = value
        return obj

    def __str__(self):
        try:
            if self.value not in MTMLError._errcode_to_string:
                MTMLError._errcode_to_string[self.value] = str(
                    mtmlErrorString(self.value)
                )
            return MTMLError._errcode_to_string[self.value]
        except MTMLError:
            return "MTML Error with code %d" % self.value

    def __eq__(self, other):
        return self.value == other.value


def _extractMTMLErrorsAsClasses():
    """
    Generates a hierarchy of classes on top of MTMLError class.

    Each MTML Error gets a new MTMLError subclass. This way try,except blocks can filter appropriate
    exceptions more easily.

    MTMLError is a parent class. Each MTML_ERROR_* gets it's own subclass.
    e.g. MTML_ERROR_ALREADY_INITIALIZED will be turned into MTMLError_AlreadyInitialized
    """
    this_module = sys.modules[__name__]
    mtmlErrorsNames = [x for x in dir(this_module) if x.startswith("MTML_ERROR_")]
    for err_name in mtmlErrorsNames:
        # e.g. Turn MTML_ERROR_ALREADY_INITIALIZED into MTMLError_AlreadyInitialized
        class_name = "MTMLError_" + string.capwords(
            err_name.replace("MTML_ERROR_", ""), "_"
        ).replace("_", "")
        err_val = getattr(this_module, err_name)

        def gen_new(val):
            def new(typ, *args):
                obj = MTMLError.__new__(typ, val)
                return obj

            return new

        new_error_class = type(class_name, (MTMLError,), {"__new__": gen_new(err_val)})
        new_error_class.__module__ = __name__
        setattr(this_module, class_name, new_error_class)
        MTMLError._valClassMapping[err_val] = new_error_class


_extractMTMLErrorsAsClasses()


def _mtmlCheckReturn(ret):
    if ret != MTML_SUCCESS:
        raise MTMLError(ret)
    return ret


## Function access ##
_mtmlGetFunctionPointer_cache = (
    dict()
)  # function pointers are cached to prevent unnecessary libLoadLock locking


def _mtmlGetFunctionPointer(name):
    global mtmlLib

    if name in _mtmlGetFunctionPointer_cache:
        return _mtmlGetFunctionPointer_cache[name]

    libLoadLock.acquire()
    try:
        # ensure library was loaded
        if mtmlLib == None:
            raise MTMLError(MTML_ERROR_FUNCTION_NOT_FOUND)
        try:
            _mtmlGetFunctionPointer_cache[name] = getattr(mtmlLib, name)
            return _mtmlGetFunctionPointer_cache[name]
        except AttributeError:
            raise MTMLError(MTML_ERROR_FUNCTION_NOT_FOUND)
    finally:
        # lock is always freed
        libLoadLock.release()


## string/bytes conversion for ease of use
def convertStrBytes(func):
    """
    In python 3, strings are unicode instead of bytes, and need to be converted for ctypes
    Args from caller: (1, 'string', <__main__.c_mtmlDevice_t at 0xFFFFFFFF>)
    Args passed to function: (1, b'string', <__main__.c_mtmlDevice_t at 0xFFFFFFFF)>
    ----
    Returned from function: b'returned string'
    Returned to caller: 'returned string'
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        # encoding a str returns bytes in python 2 and 3
        args = [arg.encode() if isinstance(arg, str) else arg for arg in args]
        res = func(*args, **kwargs)
        # In python 2, str and bytes are the same
        # In python 3, str is unicode and should be decoded.
        # Ctypes handles most conversions, this only effects c_char and char arrays.
        if isinstance(res, bytes):
            if isinstance(res, str):
                return res
            return res.decode()
        return res

    if sys.version_info >= (3,):
        return wrapper
    return func


## C function wrappers ##
def _LoadMtmlLibrary():
    """
    Load the library if it isn't loaded already
    """
    global mtmlLib

    if mtmlLib == None:
        # lock to ensure only one caller loads the library
        libLoadLock.acquire()

        try:
            # ensure the library still isn't loaded
            if mtmlLib == None:
                try:
                    # assume linux
                    mtmlLib = CDLL("libmtml.so")
                except OSError as ose:
                    _mtmlCheckReturn(MTML_ERROR_FUNCTION_NOT_FOUND)
                if mtmlLib == None:
                    _mtmlCheckReturn(MTML_ERROR_FUNCTION_NOT_FOUND)
        finally:
            # lock is always freed
            libLoadLock.release()


def mtmlLibraryInit():
    _LoadMtmlLibrary()

    #
    # Initialize the library
    #
    global libHandle
    fn = _mtmlGetFunctionPointer("mtmlLibraryInit")
    ret = fn(byref(libHandle))
    _mtmlCheckReturn(ret)

    # Atomically update refcount
    global _mtmlLib_refcount
    libLoadLock.acquire()
    _mtmlLib_refcount += 1
    libLoadLock.release()
    return None


def mtmlLibraryShutDown():
    #
    # Leave the library loaded, but shutdown the interface
    #
    global libHandle
    if libHandle is None:
        return None

    fn = _mtmlGetFunctionPointer("mtmlLibraryShutDown")
    ret = fn(libHandle)
    _mtmlCheckReturn(ret)

    # Atomically update refcount
    global _mtmlLib_refcount
    libLoadLock.acquire()
    if 0 < _mtmlLib_refcount:
        _mtmlLib_refcount -= 1
    libLoadLock.release()
    return None


@convertStrBytes
def mtmlErrorString(result):
    fn = _mtmlGetFunctionPointer("mtmlErrorString")
    fn.restype = c_char_p  # otherwise return is an int
    ret = fn(result)
    return ret


def mtmlLibraryCountDevice():
    global libHandle
    c_count = c_uint()
    fn = _mtmlGetFunctionPointer("mtmlLibraryCountDevice")
    ret = fn(libHandle, byref(c_count))
    _mtmlCheckReturn(ret)
    return c_count.value


def mtmlLibraryInitDeviceByIndex(index):
    global libHandle
    c_index = c_uint(index)
    c_device = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlLibraryInitDeviceByIndex")
    ret = fn(libHandle, c_index, byref(c_device))
    _mtmlCheckReturn(ret)
    return c_device


@convertStrBytes
def mtmlDeviceGetUUID(device):
    c_uuid = (c_char * MTML_DEVICE_UUID_BUFFER_SIZE)()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetUUID")
    ret = fn(device, byref(c_uuid), MTML_DEVICE_UUID_BUFFER_SIZE)
    _mtmlCheckReturn(ret)
    return c_uuid.value


def mtmlDeviceGetMtLinkSpec(device):
    c_mtLinkSpec = c_mtmlMtLinkSpec_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkSpec")
    ret = fn(device, byref(c_mtLinkSpec))
    _mtmlCheckReturn(ret)
    return c_mtLinkSpec


def mtmlDeviceGetMtLinkState(device, linkIndex):
    c_mtLinkState = _mtmlMtLinkState_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkState")
    ret = fn(device, linkIndex, byref(c_mtLinkState))
    _mtmlCheckReturn(ret)
    return c_mtLinkState.value


def mtmlDeviceGetMtLinkRemoteDevice(device, linkIndex):
    c_device = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkRemoteDevice")
    ret = fn(device, linkIndex, byref(c_device))
    _mtmlCheckReturn(ret)
    return c_device
