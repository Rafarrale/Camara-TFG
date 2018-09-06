#!/usr/bin/env python

import sys
import io
import os
import shutil
import base64
import json
import cv2
import paho.mqtt.client as mqtt
import picamera

from picamera.array import PiRGBArray
from picamera import PiCamera
from mail import sendEmail
from subprocess import Popen, PIPE
from string import Template
from struct import Struct
from threading import Thread
from time import sleep, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from wsgiref.simple_server import make_server
from ws4py.websocket import WebSocket
from ws4py.server.wsgirefserver import (
    WSGIServer,
    WebSocketWSGIHandler,
    WebSocketWSGIRequestHandler,
)
from ws4py.server.wsgiutils import WebSocketWSGIApplication

###########################################
# CONFIGURATION
WIDTH = 352
HEIGHT = 272
FRAMERATE = 24
HTTP_PORT = 8082
WS_PORT = 8084
COLOR = u'#444'
BGCOLOR = u'#333'
JSMPEG_MAGIC = b'jsmp'
JSMPEG_HEADER = Struct('>4sHH')
VFLIP = False
HFLIP = False

last_epoch = 0
idCamara = ""
tipo = ""
claveDisp = ""
nomCasa = ""
enCasa = False
estadoAlarma = ""
estadoAlarmaTopic = ""
usuario = "demo"
password = "demo"

def getMAC(interface='eth0'):
  # Return the MAC address of the specified interface
  try:
    str = open('/sys/class/net/%s/address' %interface).read()
  except:
    str = "00:00:00:00:00:00"
  return str[0:17]

mac = getMAC('eth0').replace(":", "")

camera = PiCamera()
camera.resolution = (WIDTH, HEIGHT)
camera.framerate = FRAMERATE
camera.vflip = VFLIP # flips image rightside up, as needed
camera.hflip = HFLIP # flips image left-right, as needed
rawCapture = PiRGBArray(camera, size=(WIDTH, HEIGHT))
object_classifier_haarcascade_upperbody = cv2.CascadeClassifier("/opt/moticasa/Smart_Security_Camera/models/haarcascade_upperbody.xml") # an opencv classifier
object_classifier_haarcascade_frontalface_alt= cv2.CascadeClassifier("/opt/moticasa/Smart_Security_Camera/models/haarcascade_frontalface_alt.xml") # an opencv classifier
email_update_interval = 10 # sends an email only once in this time interval
compruebaEnCasaInterval = 30

#MQTT Initialization
mqtt.Client.connected_flag=False #create flag in class
mqtt.Client.bad_connection_flag=False #
client = mqtt.Client("Camara", True)
print("Connecting to broker ")
try:
    client.connect("localhost",1883,60) #connect to broker
except:
    print("connection failed")
topicAlarma = 'alarma'

###########################################


class StreamingHttpHandler(BaseHTTPRequestHandler):
	def do_HEAD(self):
		self.send_response(200)
		self.send_header('Content-type', 'application/json')
		self.end_headers()
		self.do_GET()

	def do_AUTHHEAD(self):
		self.send_response(401)
		self.send_header(
			'WWW-Authenticate', 'Basic realm="Demo Realm"')
		self.send_header('Content-type', 'application/json')
		self.end_headers()

	def do_GET(self):
		
		key = self.server.get_auth_key()

		''' Present frontpage with user authentication. '''
		if self.headers.get('Authorization') == None:
			self.do_AUTHHEAD()

			response = {
				'success': False,
				'error': 'No auth header received'
			}

			self.wfile.write(bytes(json.dumps(response), 'utf-8'))

		elif self.headers.get('Authorization') == 'Basic ' + str(key):
			if self.path == '/' + idCamara:
				self.send_response(301)
				self.send_header('Location', '/opt/moticasa/pistreaming/index.html')
				self.end_headers()
				return
			elif self.path == '/opt/moticasa/pistreaming/jsmpg.js':
				content_type = 'application/javascript'
				content = self.server.jsmpg_content
			elif self.path == '/opt/moticasa/pistreaming/index.html':
				content_type = 'text/html; charset=utf-8'
				tpl = Template(self.server.index_template)
				content = tpl.safe_substitute(dict(
					WS_PORT=WS_PORT, WIDTH=WIDTH, HEIGHT=HEIGHT, COLOR=COLOR,
					BGCOLOR=BGCOLOR))
			else:
				self.send_error(404, 'File not found')
				return
			content = content.encode('utf-8')
			self.send_response(200)
			self.send_header('Content-Type', content_type)
			self.send_header('Content-Length', len(content))
			self.send_header('Last-Modified', self.date_time_string(time()))
			self.end_headers()
			if self.command == 'GET':
				self.wfile.write(content)
			
		else:
			self.do_AUTHHEAD()

			response = {
				'success': False,
				'error': 'Invalid credentials'
			}

			self.wfile.write(bytes(json.dumps(response), 'utf-8'))


class StreamingHttpServer(HTTPServer):
	key = ''
	def __init__(self):
		super(StreamingHttpServer, self).__init__(
				('', HTTP_PORT), StreamingHttpHandler)
		with io.open('/opt/moticasa/pistreaming/index.html', 'r') as f:
			self.index_template = f.read()
		with io.open('/opt/moticasa/pistreaming/jsmpg.js', 'r') as f:
			self.jsmpg_content = f.read()
				
	def set_auth(self, username, password):
		self.key = base64.b64encode(bytes('%s:%s' % (username, password), 'utf-8')).decode('ascii')

	def get_auth_key(self):
		return self.key


class StreamingWebSocket(WebSocket):
    def opened(self):
        self.send(JSMPEG_HEADER.pack(JSMPEG_MAGIC, WIDTH, HEIGHT), binary=True)


class BroadcastOutput(object):
    def __init__(self, camera):
        print('Spawning background conversion process')
        self.converter = Popen([
            'ffmpeg',
            '-f', 'rawvideo',
            '-pix_fmt', 'yuv420p',
            '-s', '%dx%d' % camera.resolution,
            '-r', str(float(camera.framerate)),
            '-i', '-',
            '-f', 'mpeg1video',
            '-b', '800k',
            '-r', str(float(camera.framerate)),
            '-'],
            stdin=PIPE, stdout=PIPE, stderr=io.open(os.devnull, 'wb'),
            shell=False, close_fds=True)

    def write(self, b):
        self.converter.stdin.write(b)

    def flush(self):
        print('Waiting for background conversion process to exit')
        self.converter.stdin.close()
        self.converter.wait()


class BroadcastThread(Thread):
    def __init__(self, converter, websocket_server):
        super(BroadcastThread, self).__init__()
        self.converter = converter
        self.websocket_server = websocket_server

    def run(self):
        try:
            while True:
                buf = self.converter.stdout.read1(32768)
                if buf:
                    self.websocket_server.manager.broadcast(buf, binary=True)
                elif self.converter.poll() is not None:
                    break
        finally:
            self.converter.stdout.close()
            
def get_object_streaming(classifier):
		# capture frames from the camera
		for frame in camera.capture_continuous(rawCapture, format="bgr", use_video_port=True):
			found_objects = False
			# grab the raw NumPy array representing the image, then initialize the timestamp
			# and occupied/unoccupied text
			image = frame.array
			
			gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
			objects = classifier.detectMultiScale(gray, 1.3, 5)
			for (x,y,w,h) in objects:
				cv2.rectangle(image,(x,y),(x+w,y+h),(255,0,0),2)
				roi_gray = gray[y:y+h, x:x+w]
				roi_color = image[y:y+h, x:x+w]
			
			if len(objects) > 0:
				found_objects = True
			
			# clear the stream in preparation for the next frame
			rawCapture.truncate(0)  
			
			ret, jpeg = cv2.imencode('.jpg', image)
			return (jpeg.tobytes(), found_objects)          

def check_for_objects():
	while True:
		global last_epoch
		if (time() - last_epoch) > compruebaEnCasaInterval and not enCasa:
			compruebaEnCasa()
		if estadoAlarma == 'armar':
			try:
				frame2, found_obj2 = get_object_streaming(object_classifier_haarcascade_upperbody)
				frame3, found_obj3 = get_object_streaming(object_classifier_haarcascade_frontalface_alt)
				if (found_obj2 or found_obj3) and (time() - last_epoch) > email_update_interval and enCasa:
					last_epoch = time()
					print ("Sending email...")
					if(found_obj3):
						sendEmail(frame3)
					elif(found_obj2):
						sendEmail(frame2)
					#Send alarm message to mqtt
					messageAlarma = 'alarma' + '#' + idCamara + '#' + 'I' + '#'
					print(messageAlarma)
					client.publish(topicAlarma, messageAlarma, 2, False)
					print ("done!")
			except:
				print ("Error sending email: "), sys.exc_info()[0]

def compruebaEnCasa():
	global last_epoch
	last_epoch = time()
	auxIdRegistra = "idRegistra" + '#' + idCamara + '#' + mac + '#'
	print("Publish: " + auxIdRegistra)
	client.publish("idRegistra", auxIdRegistra, 2, False)
	                   

def read():
	global idCamara
	global tipo
	global claveDisp
	f = open('/opt/moticasa/pistreaming/id.txt', 'r+')
	print("directorio: " + os.path.dirname(os.path.realpath(__file__)))
	#f = open('id.txt', 'r+')
	for linea in f:
		linea = linea.replace('\t', " ")
		linea = linea.replace('\n', "")
		datos = linea.split(" ")
		idCamara = datos[1]
		print("idCamara: " + idCamara)
		tipo = datos[3]
		print("tipo: " + tipo)
		claveDisp = datos[5]
		print("claveDisp: " + claveDisp)
	f.close()
		
def write(nuevoId):
	global idCamara
	idCamara = nuevoId
	f = open('/opt/moticasa/pistreaming/id.txt', 'r+')
	towrite = []
	towrite.append('id')
	towrite.append(nuevoId)
	f.write('\t'.join(towrite))
	f.close()

#MQTT Callbacks                    
def on_message(client, userdata, message):
	global enCasa
	global estadoAlarma
	sleep(1)
	messageStr = str(message.payload.decode("utf-8"))
	print("received message =",messageStr)
	if(message.topic == estadoAlarmaTopic):
		print("Estado de la alarma: " + messageStr)
		estadoAlarma = messageStr
	
	if message.topic == idCamara:
		if(messageStr == "elimina"):
			print("Creamos un nuevo disp en misDisp")
			aux = 'nuevo' + '#' + mac + '#' + tipo + '#' + claveDisp + '#'
			print(aux)
			client.publish('idRegistra',aux, 2, False)
		elif(messageStr == "201"):
			print("Camara en Dispositivos")
			enCasa = False
		else:
			print("Camara en Casa")
			#Formato mensaje recibido payload = "200#casa" --> datosHashtag = "#casa" 
			global nomCasa
			nomCasa = messageStr.replace("200#","")
			print(nomCasa)
			enCasa = True
			
			reqAlarma = 'respAlarma' + '#' + nomCasa + '#'
			print(reqAlarma)
			client.publish('respAlarma', reqAlarma, 2, False);
			
	elif message.topic == mac:
		print("Almacenamos nuevo valor de Id")
		print(messageStr)
		client.unsubscribe(idCamara)
		client.subscribe(messageStr, 2)
		write(messageStr)
		compruebaEnCasa()
    
def on_connect(client, userdata, flags, rc):
    if rc==0:
        client.connected_flag=True #set flag
        print("connected OK")
    else:
        print("Bad connection Returned code=",rc)
        client.bad_connection_flag=True

def on_disconnect(client, userdata, rc):
    print("disconnecting reason  "  +str(rc))
    client.connected_flag=False
    client.disconnect_flag=True   

###

def main():
	print('Initializing camera')
	sleep(1) # camera warm-up time
	print('Initializing websockets server on port %d' % WS_PORT)
	WebSocketWSGIHandler.http_version = '1.1'
	websocket_server = make_server(
		'', WS_PORT,
		server_class=WSGIServer,
		handler_class=WebSocketWSGIRequestHandler,
		app=WebSocketWSGIApplication(handler_cls=StreamingWebSocket))
	websocket_server.initialize_websockets_manager()
	websocket_thread = Thread(target=websocket_server.serve_forever)
	print('Initializing HTTP server on port %d' % HTTP_PORT)
	http_server = StreamingHttpServer()
	http_server.set_auth(usuario, password)
	http_thread = Thread(target=http_server.serve_forever)
	print('Initializing broadcast thread')
	output = BroadcastOutput(camera)
	broadcast_thread = BroadcastThread(output.converter, websocket_server)
	print('Starting recording')
	camera.start_recording(output, 'yuv')
	try:
		print('Starting websockets thread')
		websocket_thread.start()
		print('Starting HTTP server thread')
		http_thread.start()
		print('Starting broadcast thread')
		broadcast_thread.start()
		while True:
			camera.wait_recording(1)
	except KeyboardInterrupt:
		pass
	finally:
		print('Stopping recording')
		camera.stop_recording()
		print('Waiting for broadcast thread to finish')
		broadcast_thread.join()
		print('Shutting down HTTP server')
		http_server.shutdown()
		print('Shutting down websockets server')
		websocket_server.shutdown()
		print('Waiting for HTTP server thread to finish')
		http_thread.join()
		print('Waiting for websockets thread to finish')
		websocket_thread.join()


if __name__ == '__main__':
	t = Thread(target=check_for_objects, args=())
	t.daemon = True
	
	#MQTT init
	client.on_message=on_message
	client.on_connect=on_connect
	client.on_disconnect=on_disconnect
	client.loop_start() #start loop to process received messages
	while not client.connected_flag and not client.bad_connection_flag: #wait in loop
		print("In wait loop")
		sleep(1)
		if client.bad_connection_flag:
			client.loop_stop()    #Stop loop
			sys.exit()
	#inicio peticion id casa o dispositivos, AQUI leer de un fichero el esid
	read()
	compruebaEnCasa()
	print("subscribing Id: " + idCamara)
	client.subscribe(idCamara, 2)#subscribe
	print("subscribing Mac:" + mac)
	client.subscribe(mac, 2)#subscribe
	estadoAlarmaTopic = 'confAlarma' + '/' + mac
	print("subscribing conAlarma:" + estadoAlarmaTopic)
	client.subscribe(estadoAlarmaTopic, 2)
	###
	
	t.start()
	main()
