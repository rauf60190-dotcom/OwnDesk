"""Exercise the real JWT/JWK validation used by the Google callback, without Google requests."""
import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from apps.accounts import google


@pytest.fixture
def identity(settings):
    settings.GOOGLE_CLIENT_ID='security-test-client'
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    claims={'iss':'https://accounts.google.com','aud':settings.GOOGLE_CLIENT_ID,
            'sub':'security-test-sub','iat':int(time.time()),'exp':int(time.time())+600,
            'nonce':'security-test-nonce','email':'security@example.test','email_verified':True}
    return key,claims


def test_google_valid_identity_through_real_jwk_client(identity):
    key,claims=identity
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='security-test-key',alg='RS256',use='sig')
    client=jwt.PyJWKClient('https://www.googleapis.com/oauth2/v3/certs')
    token=jwt.encode(claims,key,algorithm='RS256',headers={'kid':jwk['kid']})
    with patch.object(google,'keys',client),patch.object(client,'fetch_data',return_value={'keys':[jwk]}) as fetch:
        assert google.verify_google_token(token,claims['nonce'])==claims
        fetch.assert_called_once()


def test_google_rejects_junk_appended_to_valid_signature(identity):
    key,claims=identity
    token=jwt.encode(claims,key,algorithm='RS256')+'!!!!'
    with patch.object(google.keys,'get_signing_key_from_jwt',return_value=SimpleNamespace(key=key.public_key())):
        with pytest.raises(jwt.PyJWTError):
            google.verify_google_token(token,claims['nonce'])


@pytest.mark.parametrize('claim',['iat','exp'])
def test_google_malformed_time_claim_is_authentication_error(identity,claim):
    key,claims=identity
    claims[claim]=[]
    token=jwt.encode(claims,key,algorithm='RS256')
    with patch.object(google.keys,'get_signing_key_from_jwt',return_value=SimpleNamespace(key=key.public_key())):
        with pytest.raises(jwt.PyJWTError):
            google.verify_google_token(token,claims['nonce'])


def test_google_deep_payload_rejected_before_fetching_keys():
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')
    token=encode(b'{"alg":"RS256","kid":"security-test-key"}')+'.'+encode(b'['*2000+b'0'+b']'*2000)+'.signature'
    with patch.object(google.keys,'fetch_data') as fetch:
        with pytest.raises(jwt.PyJWTError):
            google.verify_google_token(token,'security-test-nonce')
        fetch.assert_not_called()
