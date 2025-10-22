##
# Python bindings for the MTML library
##
from ctypes import *
from ctypes.util import find_library
from functools import wraps
import sys
import os
import threading
import string

## C Type mappings ##
## Constants
MTML_DEVICE_UUID_BUFFER_SIZE = 48

## Enums
_mtmlReturn_t = c_uint
MTML_SUCCESS                  = 0
MTML_ERROR_UNINITIALIZED      = 666
MTML_ERROR_FUNCTION_NOT_FOUND = 667
MTML_ERROR_UNKNOWN            = 999

_mtmlMtLinkState_t = c_uint
MTML_MTLINK_STATE_DOWN      = 0
MTML_MTLINK_STATE_UP        = 1
MTML_MTLINK_STATE_DOWNGRADE = 2

## Library structures
class struct_c_mtmlLibrary_t(Structure):
    pass # opaque handle
c_mtmlLibrary_t = POINTER(struct_c_mtmlLibrary_t)

## Device structures
class struct_c_mtmlDevice_t(Structure):
    pass # opaque handle
c_mtmlDevice_t = POINTER(struct_c_mtmlDevice_t)

## MtLink structures
class struct_c_mtmlMtLinkSpec_t(Structure):
    _fields_ = [
        ("version", c_uint),
        ("bandWidth", c_uint),
        ("linkNum", c_uint),
        ("rsvd", c_uint * 4),
    ]
c_mtmlMtLinkSpec_t = POINTER(struct_c_mtmlMtLinkSpec_t)

## Lib loading ##
mtmlLib = None
libLoadLock = threading.Lock()
libHandle = c_mtmlLibrary_t()
_mtmlLib_refcount = 0 # Incremented on each mtmlInit and decremented on mtmlShutdown

## Error Checking ##
class MTMLError(Exception):
    _valClassMapping = dict()
    # List of currently known error codes
    _errcode_to_string = {
        MTML_ERROR_UNINITIALIZED:       "Uninitialized",
        MTML_ERROR_FUNCTION_NOT_FOUND:  "Function Not Found",
        MTML_ERROR_UNKNOWN:             "Unknown Error",
        }
    def __new__(typ, value):
        '''
        Maps value to a proper subclass of MTMLError.
        See _extractNVMLErrorsAsClasses function for more details
        '''
        if typ == MTMLError:
            typ = MTMLError._valClassMapping.get(value, typ)
        obj = Exception.__new__(typ)
        obj.value = value
        return obj
    def __str__(self):
        try:
            if self.value not in MTMLError._errcode_to_string:
                MTMLError._errcode_to_string[self.value] = str(mtmlErrorString(self.value))
            return MTMLError._errcode_to_string[self.value]
        except MTMLError:
            return "NVML Error with code %d" % self.value
    def __eq__(self, other):
        return self.value == other.value

def _mtmlCheckReturn(ret):
    if (ret != MTML_SUCCESS):
        raise MTMLError(ret)
    return ret

## Function access ##
_mtmlGetFunctionPointer_cache = dict() # function pointers are cached to prevent unnecessary libLoadLock locking
def _mtmlGetFunctionPointer(name):
    global mtmlLib

    if name in _mtmlGetFunctionPointer_cache:
        return _mtmlGetFunctionPointer_cache[name]

    libLoadLock.acquire()
    try:
        # ensure library was loaded
        if (mtmlLib == None):
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
    '''
    In python 3, strings are unicode instead of bytes, and need to be converted for ctypes
    Args from caller: (1, 'string', <__main__.c_nvmlDevice_t at 0xFFFFFFFF>)
    Args passed to function: (1, b'string', <__main__.c_nvmlDevice_t at 0xFFFFFFFF)>
    ----
    Returned from function: b'returned string'
    Returned to caller: 'returned string'
    '''
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
    '''
    Load the library if it isn't loaded already
    '''
    global mtmlLib

    if (mtmlLib == None):
        # lock to ensure only one caller loads the library
        libLoadLock.acquire()

        try:
            # ensure the library still isn't loaded
            if (mtmlLib == None):
                try:
                    # assume linux
                    mtmlLib = CDLL("libmtml.so")
                except OSError as ose:
                    _mtmlCheckReturn(MTML_ERROR_FUNCTION_NOT_FOUND)
                if (mtmlLib == None):
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
    if (0 < _mtmlLib_refcount):
        _mtmlLib_refcount -= 1
    libLoadLock.release()
    return None

@convertStrBytes
def mtmlErrorString(result):
    fn = _mtmlGetFunctionPointer("mtmlErrorString")
    fn.restype = c_char_p # otherwise return is an int
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
    return c_mtLinkState

def mtmlDeviceGetMtLinkRemoteDevice(device, linkIndex):
    c_remoteDevice = c_mtmlDevice_t()
    fn = _mtmlGetFunctionPointer("mtmlDeviceGetMtLinkRemoteDevice")
    ret = fn(device, linkIndex, byref(c_remoteDevice))
    _mtmlCheckReturn(ret)
    return c_remoteDevice