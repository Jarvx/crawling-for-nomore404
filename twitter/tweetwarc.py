#!/usr/bin/env python
"""
Create WARC files from tweets stored in Apache Kafka in user defined dir.
Read configuration from YAML file. Run like this:
    tweetwarc.py -c tweetwarc.yaml -d /dir-to-save-warc
"""
from __future__ import unicode_literals
import argparse
import yaml
import hashlib
import uuid
import json
import logging
import os
import socket
from datetime import datetime
from time import time
from hanzo.warctools import WarcRecord
from hanzo.warctools.warc import warc_datetime_str
from kafka import KafkaConsumer

logging.basicConfig(level=logging.INFO)


def warc_uuid(text):
    """Utility method for WARC header field urn:uuid"""
    return ("<urn:uuid:%s>" %
            uuid.UUID(hashlib.sha1(text).hexdigest()[0:32])).encode('ascii')


def warc_filename(directory):
    """WARC filename example: /tmp/tweets-20170307100027-0001-fqdn.warc.gz.open
    After the file is closed, remove the .open suffix
    The filaname format is compatible with github.com/internetarchive/draintasker
    WARC_naming:   1 # {TLA}-{timestamp}-{serial}-{fqdn}.warc.gz
    """
    return "%s/tweets-%s-0001-%s.warc.gz.open" % (
        directory, datetime.utcnow().strftime('%Y%m%d%H%M%S'), socket.getfqdn())


def warcinfo_record(warc_filename):
    """Return warcinfo WarcRecord.
    Required to write in the beginning of a WARC file.
    """
    warc_date = warc_datetime_str(datetime.utcnow())
    metadata = "\r\n".join((
        "format: WARC File Format 1.0",
        "conformsTo: http://bibnum.bnf.fr/WARC/WARC_ISO_28500_version1_latestdraft.pdf"
    ))
    return WarcRecord(
        headers=[
            (WarcRecord.TYPE, WarcRecord.WARCINFO),
            (WarcRecord.CONTENT_TYPE, b'application/warc-fields'),
            (WarcRecord.ID, warc_uuid(metadata+warc_date)),
            (WarcRecord.DATE, warc_date),
            (WarcRecord.FILENAME, warc_filename)
        ],
        content=(b'application/warc-fields', metadata + "\r\n"),
        version=b"WARC/1.0"
    )


def tweet_warc_record(warc_filename, tweet_json):
    """Parse Tweet JSON and return WarcRecord.
    """
    try:
        tweet = json.loads(tweet_json)
        # skip deleted tweet
        if 'user' not in tweet:
            return
        url = "https://twitter.com/%s/status/%s" % (
            tweet['user']['screen_name'],
            tweet['id']
        )
    except Exception as ex:
        logging.error(ex)
        return

    warc_date = warc_datetime_str(datetime.utcfromtimestamp(
        float(tweet['timestamp_ms'])/1000.0))
    return WarcRecord(
        headers=[
            (WarcRecord.TYPE, WarcRecord.RESOURCE),
            (WarcRecord.CONTENT_TYPE, b'application/json'),
            (WarcRecord.ID, warc_uuid(url+warc_date)),
            (WarcRecord.URL, url),
            (WarcRecord.DATE, warc_date),
            (WarcRecord.FILENAME, warc_filename)
        ],
        content=(b'application/json', tweet_json + "\r\n"),
        version=b"WARC/1.0"
    )


parser = argparse.ArgumentParser()
parser.add_argument('-c', '--config', default='./tweetwarc.yaml',
                    help='YAML configuration file (default %(default)s)s')
parser.add_argument('-d', '--directory', default=False,
                    help='Directory to store tweets WARC.')
args = parser.parse_args()

with open(args.config) as f:
    config = yaml.load(f)

consumer = KafkaConsumer(
    bootstrap_servers=config.get('kafka_bootstrap_servers'),
    client_id=config.get('kafka_client_id'),
    group_id=config.get('kafka_group_id')
)
consumer.subscribe([config.get('kafka_topic')])

target_filename = warc_filename(args.directory)
logging.info("Archiving to file " + target_filename)
# drop .open suffix inside WARC
base_filename = os.path.basename(target_filename)[:-5]
f = open(target_filename, "ab")
record = warcinfo_record(base_filename)
record.write_to(f, gzip=True)

start_time = time()
time_limit = config.get('warc_time_limit')
size_limit = config.get('warc_size_limit')
for msg in consumer:
    tweet = msg.value.decode('utf-8').split('\n')[-2]
    record = tweet_warc_record(base_filename, tweet)
    if record:
        record.write_to(f, gzip=True)

    if os.stat(target_filename).st_size > size_limit or \
            (time() - start_time) > time_limit:
        consumer.commit()
        start_time = time()
        f.close()
        # remove .open suffix from complete WARC file
        os.rename(target_filename, target_filename[:-5])
        logging.info("Created file %s", target_filename[:-5])
        # create new file
        target_filename = warc_filename(args.directory)
        logging.info("Archiving to file " + target_filename)
        # drop .open suffix inside WARC
        base_filename = os.path.basename(target_filename)[:-5]
        f = open(target_filename, "ab")
        record = warcinfo_record(base_filename)
        record.write_to(f, gzip=True)
