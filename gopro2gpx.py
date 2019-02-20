#!/usr/bin/env python
"""Output GPS track from GoPro videos in Garmin GPX format.

Accepts an arbitrary number of input videos followed by an output file as the
command line arguments.  The GPS points from multiple videos are concatenated
into a single gpx output file. 

Adjust the FFMPEG global below if ffmpeg is in a non-standard location. This
should work on Windows as well with proper path to ffmpeg.
"""

import datetime
import struct
import sys
from subprocess import Popen, PIPE
from io import BytesIO

FFMPEG = "/usr/bin/ffmpeg"

def dump_metadata(filename):
	(o,e) = Popen([FFMPEG, '-y', '-i', filename, '-codec', 'copy', '-map', '0:3', '-f', 'rawvideo','-'],stdout=PIPE,stderr=PIPE).communicate()
	return BytesIO(o)

def gopro_binary_to_csv(gopro_binary):
    """Essentially a reimplementation of
    https://github.com/JuanIrache/gopro-utils in python.

    Takes an output file location to write to. This will parse a GoPro
    binary data file, and turn it into a CSV we can use to load data.
    That binary data file can be created with:
        ffmpeg -y -i GOPR0001.MP4 -codec copy \
                -map 0:3 -f rawvideo GOPR0001.bin
    https://pastebin.com/raw/mqbKKeSn
    """
    label_length = 4
    desc_length = 4
    # Set default scale values so we can always use it.
    scales = [1, 1, 1, 1]
    # Decide if we have a GPS fix and should start recording data.
    gps_fix = 0
    gps_accuracy = 9999
    okay_to_record = False
    data = []
    current_data = {}
    while True:
        try:
            label_string = str(gopro_binary.read(label_length))
            desc = struct.unpack('>cBBB', gopro_binary.read(desc_length))
        except struct.error as e:            
            break
        # If the first byte of the description string is zero, there
        # is no length.
        data_type = desc[0]
        if data_type == b'\x00':
            continue
        # If the label is empty, skip a packet.
        if "EMPT" in label_string:
            gopro_binary.read(4)
            continue
        # Get the size and length of data.
        val_size = desc[1]
        num_values = desc[2] << 8 | desc[3]
        data_length = val_size * num_values

        if "SCAL" in label_string:
            # Get the scale to apply to subsequent values.            
            scales = []
            for i in range(num_values):
                if val_size == 2:
                    scales.append(int(struct.unpack('>H', gopro_binary.read(2))[0]))
                elif val_size == 4:
                    scales.append(int(struct.unpack('>I', gopro_binary.read(4))[0]))
                else:
                    raise Exception("Unknown val_size for scales. Expected 2 or 4, got {}".format(val_size))
        else:
            for value in range(num_values):        
                if "GPS5" in label_string:
                    current_gps_data = {}
                    if val_size != 20:
                        raise Exception("Invalid data length for GPS5 data type. Expected 20 got {}.".format(
                            val_size))
                    latitude, longitude, altitude, speed, speed3d = struct.unpack(
                        '>iiiii', gopro_binary.read(val_size))
                    if okay_to_record:
                        current_gps_data["latitude"] = float(latitude) / scales[0]
                        current_gps_data["longitude"] = float(longitude) / scales[1]
                        current_gps_data["speedmps"] = float(speed) / scales[3]
                        current_data["gps_data"].append(current_gps_data)
                elif "GPSU" in label_string:
                    # Only append to data if we have some GPS data.
                    if current_data and current_data['gps_data']:
                        data.append(current_data)
                    timestamp = datetime.datetime.strptime(
                        gopro_binary.read(data_length).strip().decode(), '%y%m%d%H%M%S.%f')
                    current_data = {'timestamp': timestamp, 'gps_data': []}
                elif "GPSF" in label_string:                    
                    # GPS Fix. Per https://github.com/gopro/gpmf-parser:
                    # Within the GPS stream: 0 - no lock, 2 or 3 - 2D or 3D Lock.
                    gps_fix = int(struct.unpack('>I', gopro_binary.read(val_size))[0])
                elif 'GPSP' in label_string:                    
                    # GPS Accuracy. Per https://github.com/gopro/gpmf-parser:
                    # Within the GPS stream, under 500 is good.
                    gps_accuracy = int(struct.unpack('>H', gopro_binary.read(val_size))[0])                    
                else:
                    # Just skip on by the data_length, this is a data
                    # type we don't care about.            
                    gopro_binary.read(val_size)
                # Decide whether we want to record data.
                okay_to_record = gps_fix in [2, 3] and gps_accuracy < 500
                
        # Data is always packed to four bytes, so skip to the next
        # four byte chunk if we're not currently there.
        mod = data_length % 4
        if mod:
            gopro_binary.read(4 - mod)

    # Now we've got all the data, we need to populate timestamps.
 
    csv_data = []
    for index, row in enumerate(data):
        try:
            start_time = row['timestamp']
        except KeyError:
            if index == 0:
                # Let's just assume it's one second before the next
                # available timestamp.
                start_time = data[index + 1]['timestamp'] - datetime.timedelta(seconds=1)
            else:
                raise
        if index == len(data) - 1:
            end_time = start_time + datetime.timedelta(seconds=1)
        else:
            end_time = data[index + 1]['timestamp']
        step = (end_time - start_time) / len(row['gps_data'])
        for gps_index, gps_datum in enumerate(row['gps_data']):
            gps_datum['timestamp'] = (start_time + (gps_index * step))
            csv_data.append(gps_datum)
    return csv_data

def make_gpx(points, fd):
	print("""
<?xml version="1.0" encoding="UTF-8"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://www.topografix.com/GPX/1/1 http://www.topografix.com/GPX/1/1/gpx.xsd" version="1.1" creator="gopro2gpx.py">
 <trk>
  <trkseg>
  """, 
		file=fd,
	)
	for p in points:
		print(
			'   <trkpt lat="{}" lon="{}"><time>{}</time></trkpt>'.format(
				p['longitude'],
				p['latitude'],
				p['timestamp'].strftime("%Y-%m-%dT%H:%M:%SZ"),
			),		
			file=fd,
		)
	print("""
  </trkseg>
 </trk>
</gpx>
""", 
		file=fd,
	)

if __name__ == "__main__":
	if len(sys.argv) < 3:
		print("Usage: {} <video> [video...] output.gpx".format(sys.argv[0]))
		sys.exit(1)

	# create one list of all points in all of the videos on cmd line
	points = list()
	for video in sys.argv[1:len(sys.argv)-1]:
		print("Processing {}".format(video))
		points.extend(gopro_binary_to_csv(dump_metadata(VIDEO)))

	# output a simple gpx
	with open(sys.argv[-1],"w") as fd:
		print("Writing output to {}".format(sys.argv[-1]))
		make_gpx(points, fd)

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
