import praw
import emoji
import time
#import imagehash
#import requests
#from PIL import Image

id = "vF8Fjv6KyLk48A"
token =	"IsOx0WonzU3d7M5sxr5mQ1y055Y"
reddit = praw.Reddit(client_id=id,
             client_secret=token,
             user_agent='PC')

subreddit = reddit.subreddit('italy')
for comment in reddit.subreddit('italy').stream.comments(skip_existing=True):
    cbody = comment.body
    utc = comment.created_utc
    id = comment.id
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(utc))
    print(id, timestamp, cbody)
    print("-------------------------------------------------------------------")
