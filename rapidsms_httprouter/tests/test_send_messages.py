from django.test import TestCase
from mock import MagicMock, patch, Mock
from django.conf import settings

from rapidsms.backends.base import BackendBase
from rapidsms_httprouter.management.commands.send_messages import Command
from rapidsms_httprouter.models import MessageBatch, Message
from rapidsms.models import Backend, Connection


class SendMessagesCommandTestCase(TestCase):
    def setUp(self):
        self.batch1 = MessageBatch(status="Q", name="batch1", priority=1)
        self.batch1.save()

        self.command = Command()
        self.router_url = "text=%(text)s&to=%(recipient)s&smsc=%(backend)s&%(priority)s"
        self.command.fetch_url = self.fake_get_url

    def tearDown(self):
        settings.SUPPORTED_BACKENDS = None

    def fake_get_url(self, url):
        if "400" in url:
            return 403
        return 200

    def create_message(self, id, backend, batch=None):
        fake_connection = Connection(identity=str(id))
        fake_connection.backend, created = Backend.objects.get_or_create(name=backend)
        fake_connection.save()
        message = Message(status='Q', direction="O")
        message.connection = fake_connection

        message.batch = self.batch1 if batch is None else batch
        message.save()
        return message

    def create_message_without_batch(self, id, backend):
        fake_connection = Connection(identity=str(id))
        fake_connection.backend, created = Backend.objects.get_or_create(name=backend)
        fake_connection.save()
        message = Message(status='Q', direction="O")
        message.text = "this is an important message"
        message.connection = fake_connection
        message.batch = None
        message.save()
        return message

    def test_send_all_updates_status_to_sent_if_fetch_returns_200(self):
        self.command.db_key = "default"
        self.message = self.create_message(129, "fake")
        self.command.send_all(self.router_url, [self.message], 1)
        self.assertEquals((Message.objects.get(pk=self.message.pk)).status, 'S')


    def test_process_messages_for_db_processes_all_first_chunk_of_the_messages(self):
        msg1 = self.create_message(3, "fake")
        msg2 = self.create_message(2, "fake")
        self.command.process_messages_for_db(3, "default", self.router_url)
        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'S')

    def test_process_messages_can_handle_a_non_routable_backend(self):
        msg1 = self.create_message(1, "fake")
        msg2 = self.create_message(2, "fake")
        msg3 = self.create_message(400, "warid")
        msg4 = self.create_message(4, "fake")
        self.command.process_messages_for_db(5, "default", self.router_url)
        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg3.pk)).status, 'Q')
        self.assertEquals((Message.objects.get(pk=msg4.pk)).status, 'S')

    def test_process_messages_only_for_valid_backends(self):
        settings.SUPPORTED_BACKENDS = {"fake": {}, "valid_backend": {}}
        msg1 = self.create_message(1, "fake")
        msg2 = self.create_message(2, "fake")
        msg3 = self.create_message(3, "invalid")
        msg4 = self.create_message(4, "invalid")
        msg5 = self.create_message(5, "fake")
        self.command.process_messages_for_db(10, "default", self.router_url)
        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg3.pk)).status, 'B')
        self.assertEquals((Message.objects.get(pk=msg4.pk)).status, 'B')
        self.assertEquals((Message.objects.get(pk=msg5.pk)).status, 'S')

    def test_batch_is_mark_as_sent_after_marked_messages_with_invalid_backends(self):
        settings.SUPPORTED_BACKENDS = {"fake": {}, "valid_backend": {}}
        msg1 = self.create_message(1, "fake")
        msg2 = self.create_message(3, "invalid")
        msg3 = self.create_message(5, "fake")
        self.command.process_messages_for_db(10, "default", self.router_url)
        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'B')
        self.assertEquals((Message.objects.get(pk=msg3.pk)).status, 'S')
        self.command.process_messages_for_db(10, "default", self.router_url)
        self.assertEquals(MessageBatch.objects.get(pk=self.batch1.pk).status, 'C')

    def test_that_invalid_numbers_are_marked_as_blocked(self):
        settings.SUPPORTED_BACKENDS = {"valid_backend": {"identity_validation_regex": "[a-c]+"},
                                       "sms_backend": {"identity_validation_regex": "[0-9]+"}}
        msg1 = self.create_message("x", "valid_backend")
        msg2 = self.create_message("ab", "valid_backend")
        msg3 = self.create_message(4, "sms_backend")
        msg4 = self.create_message("invalid", "sms_backend")

        self.command.process_messages_for_db(10, "default", self.router_url)

        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'C')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg3.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=msg4.pk)).status, 'C')

    def test_that_message_is_not_sent_when_connection_identity_has_letters_without_valid_backends_configuration(self):
        msg1 = self.create_message("invalid", "sms_backend")
        msg2 = self.create_message(4, "sms_backend")
        self.command.process_messages_for_db(10, "default", self.router_url)
        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'Q')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'S')

    def test_that_message_is_not_sent_when_connection_identity_has_letters_and_no_validation_regex(self):
        settings.SUPPORTED_BACKENDS = {"valid_backend": {}}
        msg1 = self.create_message("x", "valid_backend")
        msg2 = self.create_message(2, "valid_backend")

        self.command.process_messages_for_db(10, "default", self.router_url)

        self.assertEquals((Message.objects.get(pk=msg1.pk)).status, 'Q')
        self.assertEquals((Message.objects.get(pk=msg2.pk)).status, 'S')

    def test_that_if_single_message_with_out_batch_is_present_it_is_sent_alongside_the_batched_messages(self):
        settings.SUPPORTED_BACKENDS = {"valid_backend": {}}
        outgoing_message_without_batch = self.create_message_without_batch("123", "valid_backend")
        outgoing_message_with_batch = self.create_message("1234", "valid_backend")
        self.command.process_messages_for_db(10, "default", self.router_url)

        self.assertEquals((Message.objects.get(pk=outgoing_message_without_batch.pk)).status, 'S')
        self.assertEquals((Message.objects.get(pk=outgoing_message_with_batch.pk)).status, 'S')

    def test_that_it_first_sends_messages_from_a_batch_with_higher_priority(self):
        batch2 = MessageBatch(status="Q", name="batch2", priority=2)
        batch2.save()
        outgoing_message_with_low_priority_batch = self.create_message("1234", "test_backend")
        outgoing_message_with_high_priority_batch = self.create_message("3331", "test_backend", batch2)

        self.command.process_messages_for_db(10, "default", self.router_url)

        self.assertEquals((Message.objects.get(pk=outgoing_message_with_low_priority_batch.pk)).status, 'Q')
        self.assertEquals((Message.objects.get(pk=outgoing_message_with_high_priority_batch.pk)).status, 'S')


class SendMessagesBackendSupportTestCase(TestCase):
    def setUp(self):
        self.command = Command()
        self.config = {
            "vumi": {
                "ENGINE": "rapidsms.backends.vumi.VumiBackend",
                "sendsms_url": "http://2.2.2.1:9000/send/",
                "sendsms_user": "username",
                "sendsms_pass": "password",
            }
        }

    def test_that_build_send_url_from_backend_gets_called(self):
        settings.BACKENDS_CONFIGURATION = self.config
        self.command.build_send_url_from_backend = MagicMock(return_value="url")
        self.command.build_send_url("url", "vumi", [], "message", 1)
        self.command.build_send_url_from_backend.assert_called_with('vumi', self.config['vumi'], 'message', [])

    @patch('requests.post')
    def test_that_fetch_url_does_a_post_if_the_url_is_a_dict(self, mock_requests):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests.return_value = mock_response
        url = {}
        self.assertEqual(200, self.command.fetch_url(url))

    @patch('urllib2.urlopen')
    def test_that_fetch_url_does_a_get_if_the_url_is_a_string(self, mock_urlopen):
        mock_response = Mock()
        mock_response.getcode.return_value = 200
        mock_urlopen.return_value = mock_response
        url = ""
        self.assertEqual(200, self.command.fetch_url(url))

    def test_get_backend_class_creates_an_instance_the_backend(self):
        backend = self.command.get_backend_class(self.config['vumi'], "vumi")
        self.assertTrue(isinstance(backend, BackendBase))

