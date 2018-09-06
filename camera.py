import cv2
from imutils.video.pivideostream import PiVideoStream
import imutils
import time
import numpy as np

class VideoCamera(object):
	

	
		
	def get_object_streaming(self, classifier):
		# capture frames from the camera
		for frame in camera.capture_continuous(rawCapture, format="bgr", use_video_port=True):
			found_objects = False
			# grab the raw NumPy array representing the image, then initialize the timestamp
			# and occupied/unoccupied text
			image = frame.array
			
			gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
			faces = face_cascade.detectMultiScale(gray, 1.3, 5)
			for (x,y,w,h) in faces:
				cv2.rectangle(image,(x,y),(x+w,y+h),(255,0,0),2)
				roi_gray = gray[y:y+h, x:x+w]
				roi_color = image[y:y+h, x:x+w]
				eyes = eye_cascade.detectMultiScale(roi_gray)
				for (ex,ey,ew,eh) in eyes:
					cv2.rectangle(roi_color,(ex,ey),(ex+ew,ey+eh),(0,255,0),2)
			
			
			# clear the stream in preparation for the next frame
			rawCapture.truncate(0)


