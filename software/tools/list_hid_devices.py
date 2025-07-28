import hid

for device in hid.enumerate():
    print(device)
