import cv2

def find_qr(img):
    qrCodeDetector = cv2.QRCodeDetector() # make this a class memebr variable
 
    decodedText, points = qrCodeDetector.detect(image) # make these class memebr variables
    
    # Make the points array an array of ints (.astype(int)) to draw lines
    if points is not None:
    
        nrOfPoints = len(points) # Is actually of shape [[[][][][]]] so use points[0] as actual array of points
    
        for i in range(nrOfPoints):
            nextPointIndex = (i+1) % nrOfPoints
            cv2.line(image, tuple(points[i][0]), tuple(points[nextPointIndex][0]), (255,0,0), 5)
    
        # print(decodedText)    
    
        # cv2.imshow("Image", image)
        # cv2.waitKey(0)
        # cv2.destroyAllWindows()
        
    
    else:
        print("QR code not detected")