#
# Sample script to demonstrate the usage of MTML API python bindings
#

# To Run:
# $ python ./example.py

from pymtml import *

#######
def deviceQuery():

    strResult = ''
    try:
        #
        # Initialize MTML
        #
        mtmlLibraryInit()

        ids = list(range(mtmlLibraryCountDevice()))
        handles = [mtmlLibraryInitDeviceByIndex(i) for i in ids]
        for i, handle in enumerate(handles):
            uuid = mtmlDeviceGetUUID(handle)
            print(f"Device {i} UUID: {uuid}")
            mtLinkSpec = mtmlDeviceGetMtLinkSpec(handle)
            print(f"Device {i} MtLink Spec: Version={mtLinkSpec.contents.version}, BandWidth={mtLinkSpec.contents.bandWidth}, LinkNum={mtLinkSpec.contents.linkNum}")
            strResult += f"Device {i} Handle: {handle}\n"

    except MTMLError as err:
        strResult += 'example.py: ' + err.__str__() + '\n'

    mtmlLibraryShutDown()

    return strResult

# If this is not executed when module is imported
if __name__ == "__main__":
    print(deviceQuery())