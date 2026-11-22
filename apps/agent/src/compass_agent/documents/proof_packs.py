"""Resolve only packaged document verifiers from the trusted assembly catalog."""

from compass_agent.documents import retail_claims

_VERIFIERS = {"retail": retail_claims.verify}


def verifier_for(name):
    return _VERIFIERS[name]
