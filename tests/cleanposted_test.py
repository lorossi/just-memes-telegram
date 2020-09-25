import datetime
import json

max_days = 14
path = "posted.json"
now = datetime.datetime.now() # current date and time
timestamp = now.strftime("%Y%m%d")
with open(path) as json_file:
    posted_data = json.load(json_file)
old_count = 0

for posted in posted_data:
    #Loop through all the posted memes and check the time at which they
    #were posted
    print(posted)
    posted_time = datetime.datetime.strptime(posted["timestamp"], "%Y%m%d")
    if (now - posted_time).days > max_days:
        #if the post has been posted too long ago, it has no sense to
        #still check if it has been posted already. Let's just mark it
        #so it can be later deleted
        old_count += 1
        posted["delete"] = True

print(old_count)
#Let's keep all the post that are not flagged and save it to file
posted_data = [x for x in posted_data if not "delete" in x]
with open(path, 'w') as outfile:
    json.dump(posted_data, outfile, indent=4)
