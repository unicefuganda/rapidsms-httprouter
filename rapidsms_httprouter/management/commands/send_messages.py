# -*- coding: utf-8 -*-

import traceback
import time
from urllib import quote_plus
import urllib2

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction, close_connection

from rapidsms_httprouter.models import Message, MessageBatch
from rapidsms.log.mixin import LoggerMixin
import requests
from rapidsms_httprouter.router import get_router
from rapidsms_httprouter_src.rapidsms_httprouter.utils import replace_characters, stringify


class Command(BaseCommand, LoggerMixin):
    help = """sends messages from all project DBs
    """

    def fetch_url(self, url):
        """
        Wrapper around url open, mostly here so we can monkey patch over it in unit tests.
        """
        self.info("URL ------------->")
        self.info(url)
        if type(url) is dict:

            r = requests.post(**url)
            code = r.status_code
            self.info(code)
        else:
            response = urllib2.urlopen(url, timeout=15)
            code = response.getcode()
            self.info(code)
        return code

    def build_send_url(self, router_url, backend, recipients_list, text, priority=1, **kwargs):
        """
        Constructs an appropriate send url for the given message.
        """
        # first build up our list of parameters
        recipients = ' '.join(recipients_list)
        special_chars_mapping = getattr(settings, "SPECIAL_CHARS_MAPPING", {})
        text = replace_characters(text, special_chars_mapping)

        installed_backends = getattr(settings, "BACKENDS_CONFIGURATION", {})

        if backend in installed_backends:
            return self.build_send_url_from_backend(backend, installed_backends[backend], text, recipients_list)
        else:
            params = {
                'backend': backend,
                'recipient': recipients,
                'text': text,
                'priority': priority,
                }

            # make sure our parameters are URL encoded
            params.update(kwargs)
            for k, v in params.items():
                params[k] = quote_plus(stringify(v))

            # is this actually a dict?  if so, we want to look up the appropriate backend
            if type(router_url) is dict:
                router_dict = router_url
                backend_name = backend

                # is there an entry for this backend?
                if backend_name in router_dict:
                    router_url = router_dict[backend_name]

                # if not, look for a default backend
                elif 'default' in router_dict:
                    router_url = router_dict['default']

                # none?  blow the hell up
                else:
                    self.error(
                        "No router url mapping found for backend '%s', check your settings.ROUTER_URL setting" % backend_name)
                    raise Exception(
                        "No router url mapping found for backend '%s', check your settings.ROUTER_URL setting" % backend_name)

        # return our built up url with all our variables substituted in
        full_url = router_url % params
        self.info("Full URL - %s" % full_url)

        return full_url

    def get_identity_validation_regex(self, backend_name):
        supported_backends = getattr(settings, 'SUPPORTED_BACKENDS', None)
        try:
            return supported_backends[backend_name]["identity_validation_regex"]
        except:
            return None

    def send_backend_chunk(self, router_url, pks, backend_name, priority):
        supported_backends = getattr(settings, 'SUPPORTED_BACKENDS', None)

        msgs = Message.objects.using(self.db_key).filter(pk__in=pks)

        if self.get_identity_validation_regex(backend_name) is None:
            msgs = msgs.exclude(connection__identity__iregex="[a-z]")

        if supported_backends is not None and backend_name not in supported_backends:
            self.info("SMS%s have unsupported backends" % pks)
            msgs.update(status='B')
            return

        try:
            recipients_list = list(msgs.values_list('connection__identity', flat=True))
            self.info("%s " % (type(recipients_list)))
            url = self.build_send_url(router_url, backend_name,recipients_list, msgs[0].text, priority=str(priority))

            status_code = self.fetch_url(url)

            # kannel likes to send 202 responses, really any
            # 2xx value means things went okay
            if int(status_code / 100) == 2:
                self.info("SMS%s SENT" % pks)
                msgs.update(status='S')
            elif int(status_code) == 403:
                self.info("SMS%s DISCARDED BY KANNEL... Taken out of queue")
                msgs.update(status='K')
            else:
                self.info("SMS%s Message not sent, got status: %s .. queued for later delivery." % (pks, status_code))
                msgs.update(status='Q')

        except Exception as e:
            self.error("SMS%s Message not sent: %s .. queued for later delivery." % (pks, str(e)))
            msgs.update(status='Q')

    def send_all(self, router_url, to_send, priority):
        pks = []
        if len(to_send):
            backend_name = to_send[0].connection.backend.name
            for msg in to_send:
                if backend_name != msg.connection.backend.name:
                    # send all of the same backend
                    self.send_backend_chunk(router_url, pks, backend_name, priority)
                    # reset the loop status variables to build the next chunk of messages with the same backend
                    backend_name = msg.connection.backend.name
                    pks = [msg.pk]
                else:
                    pks.append(msg.pk)
            self.send_backend_chunk(router_url, pks, backend_name, priority)

    def send_individual(self, router_url, priority=1):
        to_process = Message.objects.using(self.db_key).exclude(Q(text="") | Q(text=None) | Q(text=" ")).filter(
            direction='O',
            status__in=['Q'], batch=None).order_by('priority', 'status', 'connection__backend__name',
                                                   'id')  # Order by ID so that they are FIFO in absence of any other priority
        if len(to_process):
            self.debug("found [%d] individual messages to proccess, sending the first one..." % len(to_process))
            self.send_all(router_url, [to_process[0]], priority)
        else:
            self.debug("found no individual messages to process")

    def get_messages_with_invalid_identities(self, backend_name, batch):
        identity_validation_regex = self.get_identity_validation_regex(backend_name)
        if identity_validation_regex is not None:
            invalid_identity_msgs = batch.messages.filter(status='Q',
                                                          connection__backend__name=backend_name,
                                                          direction='O') \
                .exclude(connection__identity__iregex=identity_validation_regex)
            return invalid_identity_msgs
        return None

    def filter_invalid_connection_identities(self, batch):
        supported_backends = getattr(settings, "SUPPORTED_BACKENDS", None)

        if supported_backends is not None:
            for backend_name, config in supported_backends.iteritems():
                invalid_identity_msgs = self.get_messages_with_invalid_identities(backend_name, batch)

                if invalid_identity_msgs is not None:
                    invalid_identity_msgs.update(status='C')

    def process_messages_for_db(self, CHUNK_SIZE, db_key, router_url):
        self.db_key = db_key
        self.debug("looking for MessageBatch's to process with db [%s]" % str(db_key))
        blocking_batch = MessageBatch.objects.exclude(messages__status='Q').filter(status='Q')
        if blocking_batch.exists():
            self.info("Clearing %d blocking batches" % blocking_batch.count())
            blocking_batch.update(status='C')

        to_process = MessageBatch.objects.using(db_key).filter(status='Q').order_by('-priority')

        if to_process.exists():
            self.info("found [%d] batches with status [Q] in db [%s] to process" % (to_process.count(), db_key))
            try:
                batch = to_process[0]
            except IndexError:
                pass
            else:
                self.filter_invalid_connection_identities(batch)

                priority = batch.priority
                to_process = batch.messages.using(db_key).filter(direction='O',
                                                                 status__in=['Q']).order_by('priority', 'status',
                                                                                            'connection__backend__name')[
                             :CHUNK_SIZE]

                self.info("chunk of [%d] messages found in db [%s]" % (to_process.count(), db_key))
                if to_process.exists():
                    self.debug(
                        "found message batch [pk=%d] [name=%s] with Queued messages to send" % (batch.pk, batch.name))
                    self.send_all(router_url, to_process, priority)
                elif batch.messages.using(db_key).filter(status__in=['S', 'C']).count() == batch.messages.using(
                        db_key).count():
                    batch.status = 'S'
                    batch.save()
                    self.info("No more messages in MessageBatch [%d] status set to 'S'" % batch.pk)

        self.debug("Looking to see if there are any messages without a batch to send")
        self.send_individual(router_url)
        transaction.commit(using=db_key)

    def handle(self, **options):
        """

        """
        DB_KEYS = settings.DATABASES.keys()
        dbs_to_ignore = getattr(settings, 'DBS_TO_IGNORE', [])  # get the dbs to ignore
        for db in dbs_to_ignore:
            if db in DB_KEYS:
                DB_KEYS.remove(db)

        CHUNK_SIZE = getattr(settings, 'MESSAGE_CHUNK_SIZE', 400)
        self.info("starting up")
        recipients = getattr(settings, 'ADMINS', None)
        if recipients:
            recipients = [email for name, email in recipients]

        while (True):
            self.debug("send_messages started.")
            for db_key in DB_KEYS:
                try:
                    router_url = settings.DATABASES[db_key]['ROUTER_URL']

                    transaction.enter_transaction_management(using=db_key)

                    self.process_messages_for_db(CHUNK_SIZE, db_key, router_url)

                except Exception, exc:
                    print exc
                    transaction.rollback(using=db_key)
                    self.critical(traceback.format_exc(exc))
                    if recipients:
                        send_mail('[Django] Error: messenger command', str(traceback.format_exc(exc)),
                                  'root@uganda.rapidsms.org', recipients, fail_silently=True)
                    continue

            # yield from the messages table, messenger can cause
            # deadlocks if it's contanstly polling the messages table
            close_connection()
            time.sleep(0.5)

    def get_backend_class(self, backend_config, backend_name):
        path = backend_config["ENGINE"]
        module_name, class_name = path.rsplit('.', 1)
        module = __import__(module_name, globals(), locals(), [class_name])
        backend_class = getattr(module, class_name)
        router = get_router()
        backend = backend_class(router, backend_name, **backend_config)
        return backend

    def build_send_url_from_backend(self, backend_name, backend_config, text, identities):

        backend = self.get_backend_class(backend_config, backend_name)

        context = getattr(backend_config, 'context', {})

        return backend.prepare_request(0, text, identities, context)