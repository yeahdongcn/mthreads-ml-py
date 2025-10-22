##
# Python bindings for the NVML library
##
from ctypes import *
from ctypes.util import find_library
from functools import wraps
import sys
import os
import threading
import string

## C Type mappings ##
## Enums
_nvmlReturn_t = c_uint
NVML_SUCCESS                         = 0
NVML_ERROR_UNINITIALIZED             = 1
NVML_ERROR_INVALID_ARGUMENT          = 2
NVML_ERROR_NOT_SUPPORTED             = 3
NVML_ERROR_NO_PERMISSION             = 4
NVML_ERROR_ALREADY_INITIALIZED       = 5
NVML_ERROR_NOT_FOUND                 = 6
NVML_ERROR_INSUFFICIENT_SIZE         = 7
NVML_ERROR_INSUFFICIENT_POWER        = 8
NVML_ERROR_DRIVER_NOT_LOADED         = 9
NVML_ERROR_TIMEOUT                   = 10
NVML_ERROR_IRQ_ISSUE                 = 11
NVML_ERROR_LIBRARY_NOT_FOUND         = 12
NVML_ERROR_FUNCTION_NOT_FOUND        = 13
NVML_ERROR_CORRUPTED_INFOROM         = 14
NVML_ERROR_GPU_IS_LOST               = 15
NVML_ERROR_RESET_REQUIRED            = 16
NVML_ERROR_OPERATING_SYSTEM          = 17
NVML_ERROR_LIB_RM_VERSION_MISMATCH   = 18
NVML_ERROR_IN_USE                    = 19
NVML_ERROR_MEMORY                    = 20
NVML_ERROR_NO_DATA                   = 21
NVML_ERROR_VGPU_ECC_NOT_SUPPORTED    = 22
NVML_ERROR_INSUFFICIENT_RESOURCES    = 23
NVML_ERROR_FREQ_NOT_SUPPORTED        = 24
NVML_ERROR_ARGUMENT_VERSION_MISMATCH = 25
NVML_ERROR_DEPRECATED                = 26
NVML_ERROR_NOT_READY                 = 27
NVML_ERROR_GPU_NOT_FOUND             = 28
NVML_ERROR_INVALID_STATE             = 29
NVML_ERROR_RESET_TYPE_NOT_SUPPORTED  = 30
NVML_ERROR_UNKNOWN                   = 999

_nvmlGpuP2PStatus_t = c_uint
NVML_P2P_STATUS_OK     = 0
NVML_P2P_STATUS_CHIPSET_NOT_SUPPORED = 1
NVML_P2P_STATUS_CHIPSET_NOT_SUPPORTED = NVML_P2P_STATUS_CHIPSET_NOT_SUPPORED
NVML_P2P_STATUS_GPU_NOT_SUPPORTED = 2
NVML_P2P_STATUS_IOH_TOPOLOGY_NOT_SUPPORTED =3
NVML_P2P_STATUS_DISABLED_BY_REGKEY =4
NVML_P2P_STATUS_NOT_SUPPORTED =5
NVML_P2P_STATUS_UNKNOWN =6

## Lib loading ##
nvmlLib = None
libLoadLock = threading.Lock()
_nvmlLib_refcount = 0 # Incremented on each nvmlInit and decremented on nvmlShutdown

## Device structures
class struct_c_nvmlDevice_t(Structure):
    pass # opaque handle
c_nvmlDevice_t = POINTER(struct_c_nvmlDevice_t)

## Error Checking ##
class NVMLError(Exception):
    _valClassMapping = dict()
    # List of currently known error codes
    _errcode_to_string = {
        NVML_ERROR_UNINITIALIZED:       "Uninitialized",
        NVML_ERROR_INVALID_ARGUMENT:    "Invalid Argument",
        NVML_ERROR_NOT_SUPPORTED:       "Not Supported",
        NVML_ERROR_NO_PERMISSION:       "Insufficient Permissions",
        NVML_ERROR_ALREADY_INITIALIZED: "Already Initialized",
        NVML_ERROR_NOT_FOUND:           "Not Found",
        NVML_ERROR_INSUFFICIENT_SIZE:   "Insufficient Size",
        NVML_ERROR_INSUFFICIENT_POWER:  "Insufficient External Power",
        NVML_ERROR_DRIVER_NOT_LOADED:   "Driver Not Loaded",
        NVML_ERROR_TIMEOUT:             "Timeout",
        NVML_ERROR_IRQ_ISSUE:           "Interrupt Request Issue",
        NVML_ERROR_LIBRARY_NOT_FOUND:   "NVML Shared Library Not Found",
        NVML_ERROR_FUNCTION_NOT_FOUND:  "Function Not Found",
        NVML_ERROR_CORRUPTED_INFOROM:   "Corrupted infoROM",
        NVML_ERROR_GPU_IS_LOST:         "GPU is lost",
        NVML_ERROR_RESET_REQUIRED:      "GPU requires restart",
        NVML_ERROR_OPERATING_SYSTEM:    "The operating system has blocked the request.",
        NVML_ERROR_LIB_RM_VERSION_MISMATCH: "RM has detected an NVML/RM version mismatch.",
        NVML_ERROR_MEMORY:              "Insufficient Memory",
        NVML_ERROR_UNKNOWN:             "Unknown Error",
        }
    def __new__(typ, value):
        '''
        Maps value to a proper subclass of NVMLError.
        See _extractNVMLErrorsAsClasses function for more details
        '''
        if typ == NVMLError:
            typ = NVMLError._valClassMapping.get(value, typ)
        obj = Exception.__new__(typ)
        obj.value = value
        return obj
    def __str__(self):
        try:
            if self.value not in NVMLError._errcode_to_string:
                NVMLError._errcode_to_string[self.value] = str(nvmlErrorString(self.value))
            return NVMLError._errcode_to_string[self.value]
        except NVMLError:
            return "NVML Error with code %d" % self.value
    def __eq__(self, other):
        return self.value == other.value

def _nvmlCheckReturn(ret):
    if (ret != NVML_SUCCESS):
        raise NVMLError(ret)
    return ret

## Function access ##
_nvmlGetFunctionPointer_cache = dict() # function pointers are cached to prevent unnecessary libLoadLock locking
def _nvmlGetFunctionPointer(name):
    global nvmlLib

    if name in _nvmlGetFunctionPointer_cache:
        return _nvmlGetFunctionPointer_cache[name]

    libLoadLock.acquire()
    try:
        # ensure library was loaded
        if (nvmlLib == None):
            raise NVMLError(NVML_ERROR_UNINITIALIZED)
        try:
            _nvmlGetFunctionPointer_cache[name] = getattr(nvmlLib, name)
            return _nvmlGetFunctionPointer_cache[name]
        except AttributeError:
            raise NVMLError(NVML_ERROR_FUNCTION_NOT_FOUND)
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
def nvmlInitWithFlags(flags):
    _LoadNvmlLibrary()

    #
    # Initialize the library
    #
    fn = _nvmlGetFunctionPointer("nvmlInitWithFlags")
    ret = fn(flags)
    _nvmlCheckReturn(ret)

    # Atomically update refcount
    global _nvmlLib_refcount
    libLoadLock.acquire()
    _nvmlLib_refcount += 1
    libLoadLock.release()
    return None

def nvmlInit():
    nvmlInitWithFlags(0)
    return None

def _LoadNvmlLibrary():
    '''
    Load the library if it isn't loaded already
    '''
    global nvmlLib

    if (nvmlLib == None):
        # lock to ensure only one caller loads the library
        libLoadLock.acquire()

        try:
            # ensure the library still isn't loaded
            if (nvmlLib == None):
                try:
                    # assume linux
                    nvmlLib = CDLL("libnvidia-ml.so.1")
                except OSError as ose:
                    _nvmlCheckReturn(NVML_ERROR_LIBRARY_NOT_FOUND)
                if (nvmlLib == None):
                    _nvmlCheckReturn(NVML_ERROR_LIBRARY_NOT_FOUND)
        finally:
            # lock is always freed
            libLoadLock.release()

def nvmlShutdown():
    #
    # Leave the library loaded, but shutdown the interface
    #
    fn = _nvmlGetFunctionPointer("nvmlShutdown")
    ret = fn()
    _nvmlCheckReturn(ret)

    # Atomically update refcount
    global _nvmlLib_refcount
    libLoadLock.acquire()
    if (0 < _nvmlLib_refcount):
        _nvmlLib_refcount -= 1
    libLoadLock.release()
    return None

@convertStrBytes
def nvmlErrorString(result):
    fn = _nvmlGetFunctionPointer("nvmlErrorString")
    fn.restype = c_char_p # otherwise return is an int
    ret = fn(result)
    return ret

def nvmlDeviceGetHandleByIndex(index):
    c_index = c_uint(index)
    device = c_nvmlDevice_t()
    fn = _nvmlGetFunctionPointer("nvmlDeviceGetHandleByIndex_v2")
    ret = fn(c_index, byref(device))
    _nvmlCheckReturn(ret)
    return device

def nvmlDeviceGetP2PStatus(device1, device2, p2pIndex):
    c_p2pstatus = _nvmlGpuP2PStatus_t()
    fn = _nvmlGetFunctionPointer("nvmlDeviceGetP2PStatus")
    ret = fn(device1, device2,p2pIndex, byref(c_p2pstatus))
    _nvmlCheckReturn(ret)
    return c_p2pstatus.value