import requests
from PIL import Image
import imagehash
import pytesseract


def image_fingerprint(url):
    try:
        r = requests.get(url, stream=True)
        r.raw.decode_content = True # handle spurious Content-Encoding
        #Open it in PIL
        im = Image.open(r.raw)
        #Hash it
        hash = imagehash.average_hash(im)
        #OCR it
        caption = pytesseract.image_to_string(im).lower()
        #close the image
        im.close()
    except Exception as e:
        logging.error("ERROR while fingerprinting %s %s", e, url)
        return None

    return {
        "hash" : hash,
        "string_hash" : str(hash),
        "caption" : caption
    }


pytesseract.pytesseract.tesseract_cmd = str("C:\\Program Files\\Tesseract-OCR\\tesseract")

finger_1 = image_fingerprint("https://i.redd.it/vplzl09t8j841.jpg")
finger_2 = image_fingerprint("https://i.redd.it/v1bbn3ttfq741.jpg")

print(finger_1["hash"] - finger_2["hash"])
print(finger_1["caption"])
print(finger_2["caption"])
