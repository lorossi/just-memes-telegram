import json

data = {"ocr" : True}
with open("test.json", 'w') as outfile:
    json.dump(data, outfile)
