import cv2
from pyzbar.pyzbar import decode

def find_qr(img):
    qr_obj = decode(cv2.cvtColor(img, 0))
    (l, t, w, h) = qr_obj[0].rect
    cv2.rectangle(img, (l, t), (l+w, t+h), 3)