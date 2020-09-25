import datetime

status = {}
status["minutes_between_messages"] = 60
midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
now = datetime.datetime.now()
minutes_between_messages = datetime.timedelta(minutes=status["minutes_between_messages"])
next_post = midnight

while (next_post < now):
    next_post += minutes_between_messages

print(next_post)
seconds_until_next_post = (next_post - now).seconds
print(seconds_until_next_post)
