"""
Tests for inbox processing — Follow, Undo, Create, Like, Announce, Delete.
"""

from unittest.mock import MagicMock, patch

import pytest

from pubby._model import (
    InteractionType,
)
from pubby.handlers._inbox import InboxProcessor


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    storage.get_cached_actor.return_value = None
    storage.get_followers.return_value = []
    return storage


@pytest.fixture
def inbox_processor(mock_storage, private_key):
    return InboxProcessor(
        storage=mock_storage,
        actor_id="https://blog.example.com/ap/actor",
        private_key=private_key,
        key_id="https://blog.example.com/ap/actor#main-key",
    )


def _remote_actor_data(actor_id="https://remote.example.com/users/alice"):
    return {
        "id": actor_id,
        "type": "Person",
        "preferredUsername": "alice",
        "name": "Alice",
        "inbox": f"{actor_id}/inbox",
        "outbox": f"{actor_id}/outbox",
        "followers": f"{actor_id}/followers",
        "following": f"{actor_id}/following",
        "url": actor_id,
        "publicKey": {
            "id": f"{actor_id}#main-key",
            "owner": actor_id,
            "publicKeyPem": "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----",
        },
        "icon": {"type": "Image", "url": "https://remote.example.com/avatar.png"},
        "endpoints": {"sharedInbox": "https://remote.example.com/inbox"},
    }


class TestHandleFollow:
    @patch("pubby.handlers._inbox.requests")
    def test_follow_stores_follower_and_sends_accept(
        self, mock_requests, inbox_processor, mock_storage
    ):
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        # Mock fetching the actor
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp
        mock_requests.post.return_value = MagicMock(status_code=202)

        activity = {
            "id": f"{actor_id}/activities/follow-1",
            "type": "Follow",
            "actor": actor_id,
            "object": "https://blog.example.com/ap/actor",
        }

        result = inbox_processor.process(activity, skip_verification=True)

        # Should store follower
        mock_storage.store_follower.assert_called_once()
        follower = mock_storage.store_follower.call_args[0][0]
        assert follower.actor_id == actor_id
        assert follower.inbox == f"{actor_id}/inbox"
        assert follower.shared_inbox == "https://remote.example.com/inbox"

        # Should send Accept
        assert result is not None
        assert result["type"] == "Accept"

        # Should POST to the follower's inbox
        mock_requests.post.assert_called_once()


class TestHandleUndoFollow:
    def test_undo_follow_removes_follower(self, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        activity = {
            "id": f"{actor_id}/activities/undo-1",
            "type": "Undo",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/activities/follow-1",
                "type": "Follow",
                "actor": actor_id,
                "object": "https://blog.example.com/ap/actor",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.remove_follower.assert_called_once_with(actor_id)


class TestHandleUndoLike:
    def test_undo_like_deletes_interaction(self, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        target = "https://blog.example.com/post/1"
        activity = {
            "id": f"{actor_id}/activities/undo-like-1",
            "type": "Undo",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/activities/like-1",
                "type": "Like",
                "object": target,
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.delete_interaction.assert_called_once_with(
            actor_id, target, InteractionType.LIKE
        )


class TestHandleUndoBoost:
    def test_undo_announce_deletes_interaction(self, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        target = "https://blog.example.com/post/1"
        activity = {
            "id": f"{actor_id}/activities/undo-boost-1",
            "type": "Undo",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/activities/announce-1",
                "type": "Announce",
                "object": target,
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.delete_interaction.assert_called_once_with(
            actor_id, target, InteractionType.BOOST
        )


class TestHandleUndoUnknown:
    def test_undo_unknown_type_ignored(self, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        activity = {
            "id": f"{actor_id}/activities/undo-1",
            "type": "Undo",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/activities/unknown-1",
                "type": "Create",
                "object": "https://blog.example.com/post/1",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.delete_interaction.assert_not_called()
        mock_storage.remove_follower.assert_not_called()


class TestHandleCreate:
    @patch("pubby.handlers._inbox.requests")
    def test_create_note_stores_reply(
        self, mock_requests, inbox_processor, mock_storage
    ):
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/create-1",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/1",
                "type": "Note",
                "content": "<p>Great post!</p>",
                "attributedTo": actor_id,
                "inReplyTo": "https://blog.example.com/post/1",
                "published": "2024-01-01T00:00:00Z",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.source_actor_id == actor_id
        assert interaction.target_resource == "https://blog.example.com/post/1"
        assert interaction.interaction_type == InteractionType.REPLY
        assert interaction.content == "<p>Great post!</p>"
        assert interaction.author_name == "Alice"

    @patch("pubby.handlers._inbox.requests")
    def test_create_without_reply_to_ignored(self, inbox_processor, mock_storage):
        activity = {
            "id": "https://remote.example.com/activities/1",
            "type": "Create",
            "actor": "https://remote.example.com/users/alice",
            "object": {
                "id": "https://remote.example.com/notes/1",
                "type": "Note",
                "content": "Just a random note",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_not_called()


class TestHandleQuote:
    """Tests for incoming quotes (Create) and QuoteRequest approval."""

    @patch("pubby.handlers._inbox.requests")
    def test_create_with_quote_stores_quote_interaction(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """A Create with a quoteUrl should store a QUOTE interaction."""
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/create-q1",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/q1",
                "type": "Note",
                "content": "<p>Quoting this!</p>",
                "attributedTo": actor_id,
                "quoteUrl": "https://blog.example.com/post/1",
                "published": "2024-01-01T00:00:00Z",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.interaction_type == InteractionType.QUOTE
        assert interaction.target_resource == "https://blog.example.com/post/1"
        assert interaction.object_id == f"{actor_id}/notes/q1"

    @patch("pubby.handlers._inbox.requests")
    def test_create_with_quote_does_not_send_authorization(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """Create with quoteUrl stores interaction but does NOT deliver anything."""
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/create-q2",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/q2",
                "type": "Note",
                "content": "<p>Quoting your article</p>",
                "attributedTo": actor_id,
                "quoteUrl": "https://blog.example.com/article/test",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()
        # Create no longer sends anything — approval goes through QuoteRequest
        mock_requests.post.assert_not_called()

    @patch("pubby.handlers._inbox.requests")
    def test_create_with_fep0449_quote_field(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """The FEP-044f 'quote' field should also be recognized."""
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/create-q4",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/q4",
                "type": "Note",
                "content": "<p>FEP quote</p>",
                "attributedTo": actor_id,
                "quote": "https://blog.example.com/post/2",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.interaction_type == InteractionType.QUOTE
        assert interaction.target_resource == "https://blog.example.com/post/2"

    @patch("pubby.handlers._inbox.requests")
    def test_create_with_misskey_quote_field(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """Misskey's '_misskey_quote' field should also be recognized."""
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/create-q5",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/q5",
                "type": "Note",
                "content": "<p>Misskey quote</p>",
                "attributedTo": actor_id,
                "_misskey_quote": "https://blog.example.com/post/3",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.interaction_type == InteractionType.QUOTE
        assert interaction.target_resource == "https://blog.example.com/post/3"

    @patch("pubby.handlers._inbox.requests")
    def test_quote_takes_precedence_over_reply(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """When both inReplyTo and quoteUrl are present, it's a QUOTE."""
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/create-q6",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/q6",
                "type": "Note",
                "content": "<p>Both reply and quote</p>",
                "attributedTo": actor_id,
                "inReplyTo": "https://blog.example.com/post/1",
                "quoteUrl": "https://blog.example.com/post/2",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.interaction_type == InteractionType.QUOTE
        assert interaction.target_resource == "https://blog.example.com/post/2"


class TestHandleQuoteRequest:
    """Tests for incoming QuoteRequest activities (FEP-044f)."""

    @patch("pubby.handlers._inbox.requests")
    def test_quote_request_sends_accept_with_authorization(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """QuoteRequest should produce an Accept with result pointing to a
        stored QuoteAuthorization."""
        actor_id = "https://remote.example.com/users/bob"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp
        mock_requests.post.return_value = MagicMock(status_code=202)

        activity = {
            "id": f"{actor_id}/statuses/1/quote",
            "type": "QuoteRequest",
            "actor": actor_id,
            "object": "https://blog.example.com/post/1",
            "instrument": f"{actor_id}/statuses/1",
        }

        result = inbox_processor.process(activity, skip_verification=True)

        # Should return the Accept activity
        assert result is not None
        assert result["type"] == "Accept"
        assert result["actor"] == "https://blog.example.com/ap/actor"
        assert result["to"] == actor_id
        assert result["object"] == activity

        # result should be a QuoteAuthorization URL
        auth_id = result["result"]
        assert "quote_authorizations" in auth_id

        # QuoteAuthorization should be stored
        mock_storage.store_quote_authorization.assert_called_once()
        stored_id, stored_data = mock_storage.store_quote_authorization.call_args[0]
        assert stored_id == auth_id
        assert stored_data["type"] == "QuoteAuthorization"
        assert stored_data["attributedTo"] == "https://blog.example.com/ap/actor"
        assert stored_data["interactionTarget"] == "https://blog.example.com/post/1"
        assert stored_data["interactingObject"] == f"{actor_id}/statuses/1"

        # Accept should be POSTed to the actor's inbox
        import json

        mock_requests.post.assert_called_once()
        call_kwargs = mock_requests.post.call_args
        posted_url = call_kwargs[0][0]
        assert posted_url == f"{actor_id}/inbox"
        payload = json.loads(call_kwargs[1]["data"])
        assert payload["type"] == "Accept"
        assert payload["result"] == auth_id

    @patch("pubby.handlers._inbox.requests")
    def test_quote_request_with_embedded_instrument(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """QuoteRequest with instrument as an embedded object."""
        actor_id = "https://remote.example.com/users/bob"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp
        mock_requests.post.return_value = MagicMock(status_code=202)

        activity = {
            "id": f"{actor_id}/statuses/2/quote",
            "type": "QuoteRequest",
            "actor": actor_id,
            "object": "https://blog.example.com/post/2",
            "instrument": {
                "type": "Note",
                "id": f"{actor_id}/statuses/2",
                "attributedTo": actor_id,
                "content": "<p>Quoting your post</p>",
                "quote": "https://blog.example.com/post/2",
            },
        }

        result = inbox_processor.process(activity, skip_verification=True)
        assert result is not None
        assert result["type"] == "Accept"

        mock_storage.store_quote_authorization.assert_called_once()
        _, stored_data = mock_storage.store_quote_authorization.call_args[0]
        assert stored_data["interactingObject"] == f"{actor_id}/statuses/2"

    @patch("pubby.handlers._inbox.requests")
    def test_quote_request_ignored_when_disabled(
        self, mock_requests, mock_storage, private_key
    ):
        """When auto_approve_quotes is False, QuoteRequest is ignored."""
        processor = InboxProcessor(
            storage=mock_storage,
            actor_id="https://blog.example.com/ap/actor",
            private_key=private_key,
            key_id="https://blog.example.com/ap/actor#main-key",
            auto_approve_quotes=False,
        )

        actor_id = "https://remote.example.com/users/bob"

        activity = {
            "id": f"{actor_id}/statuses/3/quote",
            "type": "QuoteRequest",
            "actor": actor_id,
            "object": "https://blog.example.com/post/1",
            "instrument": f"{actor_id}/statuses/3",
        }

        result = processor.process(activity, skip_verification=True)
        assert result is None
        mock_requests.post.assert_not_called()
        mock_storage.store_quote_authorization.assert_not_called()

    @patch("pubby.handlers._inbox.requests")
    def test_quote_request_missing_instrument_ignored(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """QuoteRequest without instrument is ignored."""
        actor_id = "https://remote.example.com/users/bob"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/statuses/4/quote",
            "type": "QuoteRequest",
            "actor": actor_id,
            "object": "https://blog.example.com/post/1",
        }

        result = inbox_processor.process(activity, skip_verification=True)
        assert result is None
        mock_storage.store_quote_authorization.assert_not_called()

    @patch("pubby.handlers._inbox.requests")
    def test_quote_authorization_context_matches_fep044f(
        self, mock_requests, inbox_processor, mock_storage
    ):
        """Stored QuoteAuthorization should have the FEP-044f context."""
        actor_id = "https://remote.example.com/users/bob"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp
        mock_requests.post.return_value = MagicMock(status_code=202)

        activity = {
            "id": f"{actor_id}/statuses/5/quote",
            "type": "QuoteRequest",
            "actor": actor_id,
            "object": "https://blog.example.com/post/1",
            "instrument": f"{actor_id}/statuses/5",
        }

        inbox_processor.process(activity, skip_verification=True)
        _, stored_data = mock_storage.store_quote_authorization.call_args[0]

        ctx = stored_data["@context"]
        assert ctx[0] == "https://www.w3.org/ns/activitystreams"
        ext = ctx[1]
        assert (
            ext["QuoteAuthorization"] == "https://w3id.org/fep/044f#QuoteAuthorization"
        )
        assert "interactingObject" in ext
        assert "interactionTarget" in ext


class TestExtractQuoteTarget:
    """Unit tests for the static _extract_quote_target helper."""

    def test_no_quote_fields(self):
        assert InboxProcessor._extract_quote_target({}) is None

    def test_quote_field(self):
        assert (
            InboxProcessor._extract_quote_target(
                {"quote": "https://example.com/post/1"}
            )
            == "https://example.com/post/1"
        )

    def test_quote_url_field(self):
        assert (
            InboxProcessor._extract_quote_target(
                {"quoteUrl": "https://example.com/post/2"}
            )
            == "https://example.com/post/2"
        )

    def test_misskey_quote_field(self):
        assert (
            InboxProcessor._extract_quote_target(
                {"_misskey_quote": "https://example.com/post/3"}
            )
            == "https://example.com/post/3"
        )

    def test_priority_order(self):
        data = {
            "quote": "https://example.com/fep",
            "quoteUrl": "https://example.com/mastodon",
        }
        assert InboxProcessor._extract_quote_target(data) == "https://example.com/fep"

    def test_empty_string_skipped(self):
        assert (
            InboxProcessor._extract_quote_target({"quote": "", "quoteUrl": ""}) is None
        )


class TestHandleLike:
    @patch("pubby.handlers._inbox.requests")
    def test_like_stores_interaction(
        self, mock_requests, inbox_processor, mock_storage
    ):
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/like-1",
            "type": "Like",
            "actor": actor_id,
            "object": "https://blog.example.com/post/1",
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.interaction_type == InteractionType.LIKE
        assert interaction.target_resource == "https://blog.example.com/post/1"


class TestHandleAnnounce:
    @patch("pubby.handlers._inbox.requests")
    def test_announce_stores_boost(self, mock_requests, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/announce-1",
            "type": "Announce",
            "actor": actor_id,
            "object": "https://blog.example.com/post/2",
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.interaction_type == InteractionType.BOOST
        assert interaction.target_resource == "https://blog.example.com/post/2"


class TestHandleDelete:
    def test_delete_removes_interactions_by_object_id(
        self, inbox_processor, mock_storage
    ):
        actor_id = "https://remote.example.com/users/alice"
        object_id = f"{actor_id}/notes/1"
        activity = {
            "id": f"{actor_id}/activities/delete-1",
            "type": "Delete",
            "actor": actor_id,
            "object": {
                "id": object_id,
                "type": "Tombstone",
            },
        }

        # When object_id lookup succeeds, don't fall back to brute-force
        mock_storage.delete_interaction_by_object_id.return_value = True
        inbox_processor.process(activity, skip_verification=True)
        mock_storage.delete_interaction_by_object_id.assert_called_once_with(
            actor_id, object_id
        )
        mock_storage.delete_interaction.assert_not_called()

    def test_delete_falls_back_to_brute_force(self, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        object_id = f"{actor_id}/notes/1"
        activity = {
            "id": f"{actor_id}/activities/delete-1",
            "type": "Delete",
            "actor": actor_id,
            "object": {
                "id": object_id,
                "type": "Tombstone",
            },
        }

        # When object_id lookup fails, fall back to trying all types
        mock_storage.delete_interaction_by_object_id.return_value = False
        inbox_processor.process(activity, skip_verification=True)
        assert mock_storage.delete_interaction.call_count == len(InteractionType)

    def test_delete_with_string_object(self, inbox_processor, mock_storage):
        actor_id = "https://remote.example.com/users/alice"
        object_id = f"{actor_id}/notes/1"
        activity = {
            "id": f"{actor_id}/activities/delete-2",
            "type": "Delete",
            "actor": actor_id,
            "object": object_id,  # string, not dict
        }

        mock_storage.delete_interaction_by_object_id.return_value = True
        inbox_processor.process(activity, skip_verification=True)
        mock_storage.delete_interaction_by_object_id.assert_called_once_with(
            actor_id, object_id
        )


class TestHandleUpdate:
    @patch("pubby.handlers._inbox.requests")
    def test_update_note_updates_interaction(
        self, mock_requests, inbox_processor, mock_storage
    ):
        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/update-1",
            "type": "Update",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/1",
                "type": "Note",
                "content": "<p>Updated reply</p>",
                "inReplyTo": "https://blog.example.com/post/1",
            },
        }

        inbox_processor.process(activity, skip_verification=True)
        mock_storage.store_interaction.assert_called_once()

        interaction = mock_storage.store_interaction.call_args[0][0]
        assert interaction.content == "<p>Updated reply</p>"


class TestUnknownActivity:
    def test_unknown_type_ignored(self, inbox_processor, mock_storage):
        activity = {
            "id": "https://example.com/activity/1",
            "type": "Add",
            "actor": "https://example.com/actor",
            "object": "something",
        }

        result = inbox_processor.process(activity, skip_verification=True)
        assert result is None
        mock_storage.store_follower.assert_not_called()
        mock_storage.store_interaction.assert_not_called()


class TestInteractionCallback:
    @patch("pubby.handlers._inbox.requests")
    def test_callback_called_on_like(self, mock_requests, mock_storage, private_key):
        callback = MagicMock()
        processor = InboxProcessor(
            storage=mock_storage,
            actor_id="https://blog.example.com/ap/actor",
            private_key=private_key,
            key_id="https://blog.example.com/ap/actor#main-key",
            on_interaction_received=callback,
        )

        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        activity = {
            "id": f"{actor_id}/activities/like-1",
            "type": "Like",
            "actor": actor_id,
            "object": "https://blog.example.com/post/1",
        }

        processor.process(activity, skip_verification=True)
        callback.assert_called_once()

    @patch("pubby.handlers._inbox.requests")
    def test_callback_called_on_quote(self, mock_requests, mock_storage, private_key):
        callback = MagicMock()
        processor = InboxProcessor(
            storage=mock_storage,
            actor_id="https://blog.example.com/ap/actor",
            private_key=private_key,
            key_id="https://blog.example.com/ap/actor#main-key",
            on_interaction_received=callback,
        )

        actor_id = "https://remote.example.com/users/alice"
        actor_data = _remote_actor_data(actor_id)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = actor_data
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp
        mock_requests.post.return_value = MagicMock(status_code=202)

        activity = {
            "id": f"{actor_id}/activities/create-q-cb",
            "type": "Create",
            "actor": actor_id,
            "object": {
                "id": f"{actor_id}/notes/q-cb",
                "type": "Note",
                "content": "<p>Quoted!</p>",
                "attributedTo": actor_id,
                "quoteUrl": "https://blog.example.com/post/1",
            },
        }

        processor.process(activity, skip_verification=True)
        callback.assert_called_once()
        interaction = callback.call_args[0][0]
        assert interaction.interaction_type == InteractionType.QUOTE
