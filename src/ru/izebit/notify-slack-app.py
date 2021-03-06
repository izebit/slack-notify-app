#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import datetime
import json
import time
import urllib.parse
import urllib.request
from urllib.parse import urlencode
from urllib.request import Request, urlopen

__author__ = 'Artem Konovalov <a.konovalov@fasten.com>'
__version__ = '1.0'

STOP_WORDS = ['AuthenticationException', 'SocketException']

SEVERITY_LIST = ['error']
DUPLICATE_THRESHOLD = 10
DELAY_SECONDS = 600

RECIPIENTS = {
    '<service-name>': '@<slack-nickname>',
}


class SlackSender:
    _bot_token = None
    _channel_name = None
    _channel_web_hook_url = None

    def __init__(self, bot_token, channel_web_hook_url, channel_name):
        self._bot_token = bot_token
        self._channel_name = channel_name
        self._channel_web_hook_url = channel_web_hook_url

    def _send_msg(self, title, text, pretext, color):
        url = 'https://hooks.slack.com/services/' + self._channel_web_hook_url
        data = {
            "attachments": [
                {
                    "title": title,
                    "color": color,
                    "pretext": pretext,
                    "text": text
                }
            ]
        }
        request_body = json.dumps(data).encode('utf-8')
        headers = {
            'Content-Type': 'application/json',
            'Content-Length': len(request_body)
        }

        request = Request(url, request_body, headers)
        urlopen(request).read().decode()

    def send_info(self, text):
        self._send_msg("Hi", text, ":rocket:", 'good')

    def send_error(self, text):
        self._send_msg("Error:", text, "There is something wrong with me :warning:", 'danger')

    def send_data(self, items):
        print(">> size:{}".format(len(items)))

        if len(items) == 0:
            return

        for app_name, logs in items.items():
            print(app_name)
            for log in logs:
                url = 'https://slack.com/api/files.upload'
                recipients = RECIPIENTS.get(app_name.lower(), "anonymous")

                request_params = {
                    'channels': self._channel_name,
                    'token': self._bot_token,
                    'filetype': 'java',
                    'title': "{}-{}".format(log.application, log.severity),
                    'filename': '{}.log'.format(app_name),
                    'content': '{}\n{}'.format(log.message, log.stacktrace),
                    'initial_comment': 'application: {} \nmembers: {}\ndate: {}'.format(log.application,
                                                                                        recipients,
                                                                                        log.date.strftime(
                                                                                            "%Y-%m-%d %H:%M"))
                }

                request = Request(url, urlencode(request_params).encode())
                response = urlopen(request).read().decode()
                print(response)


class Log:
    severity = None
    application = None
    message = None
    stacktrace = None
    date = None

    def __init__(self, application, severity, message, stacktrace, timestamp):
        self.application = application
        self.severity = severity
        self.message = message
        self.stacktrace = '' if stacktrace is None else stacktrace
        self.date = Log._parse_timestamp(timestamp)

    def __eq__(self, other):
        if not isinstance(other, Log.__class__):
            return False

        return (self.application == other.application) and \
               (self.severity == other.severity) and \
               (self.date == other.date) and \
               (self.message == self.message) and \
               (self.stacktrace == self.stacktrace)

    def __hash__(self):
        return hash(self.severity) + \
               hash(self.application) + \
               hash(self.message) + \
               hash(self.date) + \
               hash(self.stacktrace)

    @staticmethod
    def _parse_timestamp(dt_str):
        try:
            dt = datetime.datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S.%fZ")
        except Exception:
            try:
                dt = datetime.datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ")
            except Exception:
                return datetime.datetime.today()
        return dt

    @staticmethod
    def _is_duplicate(s1, s2, threshold):
        if s1 is None or s2 is None:
            return False

        s1 = s1.lower()
        s2 = s2.lower()

        min_len = min(len(s1), len(s2))
        result = 0
        count = 0
        for index in range(0, min_len):
            if s1[index] == s2[index]:
                count += 1
                if count >= threshold:
                    return True
            else:
                result = max(result, count)
                count = 0

        return max(result, count) > threshold

    @staticmethod
    def remove_useless_logs(items):
        tmp_set = set(items)
        for i in range(0, len(items)):
            for j in range(i + 1, len(items)):

                if Log._is_duplicate(items[i].message, items[j].message, DUPLICATE_THRESHOLD) and items[j] in tmp_set:
                    tmp_set.remove(items[j])

        items.clear()
        items.extend(tmp_set)


class ElasticSearchLoader:
    _server_url = None
    _last_update_time = None

    def __init__(self, server_url):
        self._last_update_time = datetime.datetime.today()
        self._server_url = server_url

    @staticmethod
    def _get_query_for_stop_word():
        query = ""

        for word in STOP_WORDS:
            query += " *" + word + "* "

        return query

    def _load_json(self, limit=100):
        body = {
            "query": {
                "bool": {
                    "filter": {
                        "terms": {"severity": SEVERITY_LIST}
                    },
                    "must": {
                        "range": {
                            "@timestamp": {
                                "gt": self._last_update_time.strftime("%Y-%m-%d %H:%M:%S.%f"),
                                "format": "yyyy-MM-dd HH:mm:ss.SSSSSS"
                            }
                        }
                    },
                    "must_not": {
                        "query_string": {
                            "default_field": "_all",
                            "query": ElasticSearchLoader._get_query_for_stop_word(),
                            "analyze_wildcard": True
                        }
                    }
                }
            },
            "sort": [
                {"@timestamp": {"order": "asc"}}
            ],
            "from": 0, "size": limit,
            "_source": ["severity", "application", "message", "stacktrace", "@timestamp"]
        }
        request_body = json.dumps(body).encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'Content-Length': len(request_body)
        }

        url = self._server_url + 'logs-*/_search'
        request = urllib.request.Request(url, 'GET', headers)
        response = urllib.request.urlopen(request, request_body, timeout=10000).read().decode('utf-8')
        print("<< {}".format(str(response)))
        return json.loads(response)['hits']['hits']

    @staticmethod
    def _parse(data):
        result = []

        for entry in data:
            information = entry['_source']
            application = information['application']
            severity = information['severity']
            message = information.get('message', '')
            stacktrace = information.get('stacktrace', '')
            timestamp = information['@timestamp']
            log = Log(application, severity, message, stacktrace, timestamp)
            result.append(log)

        return result

    def load(self):
        print("send : {}".format(self._last_update_time.strftime('%Y-%m-%d %H:%M:%S.%f')))
        result = {}

        while True:

            data = self._load_json()
            if len(data) == 0:
                break

            logs = ElasticSearchLoader._parse(data)
            for log in logs:
                result.setdefault(log.application, [])
                result.get(log.application).append(log)
                self._last_update_time = max(log.date, self._last_update_time)

            print(self._last_update_time.strftime('%Y-%m-%d %H:%M:%S.%f'))

        for log_list in result.values():
            Log.remove_useless_logs(log_list)

        return result


class Watcher:
    _sender = None
    _loader = None

    def __init__(self, consumer, producer):
        self._loader = producer
        self._sender = consumer

    def watcher(self):
        self._sender.send_info("i started to work")

        while True:
            logs = {}
            try:
                logs = self._loader.load()
            except Exception as e:
                print('error happened while loading logs from elastic search: {}'.format(e))
                self._sender.send_error('error happened while loading logs from elastic search:{}'.format(e))

            try:
                self._sender.send_data(logs)
            except Exception as e:
                print('error happened while sending notifies about errors: {}'.format(e))
                self._sender.send_error('error happened while sending notifies about errors: {}'.format(e))

            time.sleep(DELAY_SECONDS)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='slack notify demon', prefix_chars='-+')
    parser.add_argument('--elastic-search-domain', required=True, help='domain of elastic search')
    parser.add_argument('--slack-channel', required=True, help='slack channels which daemon will sends notify to')
    parser.add_argument('--slack-channel-web-hook-url', required=True, help='access token to slack for channel')
    parser.add_argument('--slack-bot-token', required=True, help='access token to slack for bot')

    args = parser.parse_args()
    sender = SlackSender(args.slack_bot_token,
                         args.slack_channel_web_hook_url,
                         args.slack_channel)

    loader = ElasticSearchLoader(args.elastic_search_domain)

    watcher = Watcher(sender, loader)
    watcher.watcher()
