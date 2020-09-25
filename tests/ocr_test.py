from PIL import Image
import pytesseract
import requests
import imagehash

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract'

url = "https://i.redd.it/vrzw1ro5vk841.png"
r = requests.get(url, stream=True)
r.raw.decode_content = True # handle spurious Content-Encoding
im = Image.open(r.raw)
hash = str(imagehash.average_hash(im))
string = pytesseract.image_to_string(im)

print(hash, string)
im.close()
