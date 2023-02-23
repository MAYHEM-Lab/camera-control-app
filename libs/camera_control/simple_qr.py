import cv2
from pyzbar.pyzbar import decode

def find_qr(img):
    detector = cv2.QRCodeDetector()
    ret, pts = detector.detect(img)
    cv2.rectangle(img, pts[0], pts[2], 2)