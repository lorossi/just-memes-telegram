import json

path = "settings.json"
with open(path) as json_file:
    settings = json.load(json_file)

print(settings)
