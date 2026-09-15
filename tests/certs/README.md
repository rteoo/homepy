# Synthetic TLS fixture

`localhost-cert.pem` and `localhost-key.pem` are a public, test-only self-signed
certificate and matching private key. They authorize no deployment or account.
They exist only to exercise certificate trust and hostname verification over
loopback TLS. Never use this key outside tests. The certificate names localhost
and expires in September 2036; regenerate the pair before expiry.
