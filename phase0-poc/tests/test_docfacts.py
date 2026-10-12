"""The documentation-pin tests.

These do not test behaviour. They assert that the facts the design rests on are recorded,
attributable, and correctly classified - so that an unverifiable claim can never quietly
masquerade as a documented one.

`evidence == "documented"` entries must carry a quotation and an official URL.
`"contradictory"` and `"undocumented"` entries must NOT be presented as facts elsewhere in
the PoC, and must appear in the Phase 0 report as gates rather than assertions.
"""

from __future__ import annotations

from urllib.parse import urlparse

from hexton_poc import docfacts
from hexton_poc.docfacts import DOC_FACTS

OFFICIAL_HOSTS = {"developers.google.com", "support.google.com", "developers.google.cn"}


def test_fact_ids_are_unique_and_prefixed():
    ids = [f.id for f in DOC_FACTS]
    assert len(ids) == len(set(ids)), "duplicate fact ids"
    assert all(i.startswith("DF-") for i in ids)


def test_every_fact_has_a_source_url_on_an_official_host():
    for f in DOC_FACTS:
        host = urlparse(f.url).netloc
        assert host in OFFICIAL_HOSTS, f"{f.id} cites a non-official host: {f.url}"


def test_documented_facts_carry_a_quotation():
    for f in DOC_FACTS:
        if f.evidence == "documented":
            assert f.quote.strip(), f"{f.id} is classified documented but quotes nothing"
            assert f.claim.strip(), f"{f.id} has no claim"


def test_non_documented_facts_are_explicitly_classified():
    for f in DOC_FACTS:
        assert f.evidence in {"documented", "contradictory", "undocumented"}
        if f.evidence != "documented":
            # They must explain themselves rather than hide behind a quotation.
            assert f.quote.strip(), f"{f.id} must record what is absent or contradictory"


def test_the_unverified_project_contradiction_is_pinned_as_a_contradiction():
    fact = docfacts.get("DF-25")
    assert fact.evidence == "contradictory"
    # Both halves of the contradiction must be present, or the record is misleading.
    assert "private by default" in fact.quote
    assert "not restricted to private viewing mode" in fact.quote


def test_the_three_unknowns_are_marked_unknown_not_documented():
    for fid in ("DF-26", "DF-27", "DF-28"):
        assert docfacts.get(fid).evidence == "undocumented", f"{fid} must not be documented"


def test_no_undocumented_claim_is_used_as_if_it_were_settled():
    """Guards the specific over-claims this PoC must not reintroduce."""
    unsettled = {f.id for f in DOC_FACTS if f.evidence != "documented"}

    # Minimum publishAt lead time: must remain a configured constant, never a "Google limit".
    from hexton_poc import metadata

    assert metadata.DEFAULT_MIN_LEAD_MINUTES > 0
    assert "DF-26" in unsettled

    # Brand-account publication: the identity module must say it is unproven.
    from hexton_poc.youtube import identity

    assert "FEAS-01" in identity.brand_account_note()
    assert "DF-27" in unsettled


def test_managd_by_me_is_recorded_as_partner_only():
    """Regression: an earlier draft treated this as a brand-account path."""
    assert docfacts.get("DF-09").evidence == "documented"
    assert docfacts.get("DF-09").quote.startswith("managedByMe boolean")
    assert "content partners" in docfacts.get("DF-09").quote


def test_fact_count_is_stable_enough_to_be_a_pin():
    """Not a snapshot test of content - just that nothing silently disappears."""
    assert len(DOC_FACTS) >= 28
