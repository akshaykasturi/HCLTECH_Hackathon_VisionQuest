"""Similar-ticket search (app/tickets/similar.py) on 8 made-up tickets. Offline: a fake embedder, no model."""
import numpy as np
import pytest

from app.common.doc_search import tokenize
from app.tickets import similar
from app.tickets.models import TicketFilters
from app.tickets.similar import build_ticket_index, retrieval_eval, search_similar


def ticket(tid, split, category, subcategory, service, group, subject, description, created="2026-01-10T09:00:00",
           notes="", steps="", problem_id=""):
    return {"ticket_id": tid, "created_at": created, "resolved_at": "", "status": "Resolved", "channel": "portal",
            "category": category, "subcategory": subcategory, "priority": "P3", "assigned_team": "Service Desk",
            "affected_service": service, "environment": "laptop", "subject": subject, "description": description,
            "root_cause": "", "resolution_notes": notes, "resolution_steps": steps, "problem_id": problem_id,
            "dup_group_id": group, "split": split}


ROWS = [
    ticket("INC1", "train", "Network", "VPN", "VPN", "G1", "VPN error 809 from home",
           "The VPN client fails with error 809 when connecting from home.",
           notes="Called jane.doe@example.com and reset the VPN profile.", steps="Reset VPN profile | Reconnect",
           problem_id="PRB-12"),
    ticket("INC2", "val", "Email", "Outlook", "Email", "G2", "Outlook keeps asking for password",
           "Outlook prompts for the password every few minutes.", created="2026-03-01T10:00:00"),
    ticket("INC3", "train", "Access", "Password Reset", "Active Directory", "G3", "VPN password expired",
           "My VPN password expired and I cannot sign in."),
    ticket("INC4", "train", "Hardware", "Printer", "Printing", "G4", "Printer paper jam on floor 2",
           "The printer shows a paper jam and will not print.", created="2026-02-01T10:00:00"),
    ticket("INC5", "train", "Email", "Mailbox", "Email", "G5", "Mailbox full",
           "My mailbox is full and I cannot receive email."),
    ticket("INC6", "train", "Hardware", "Laptop", "Laptop", "G6", "Laptop disk full",
           "The laptop disk is full, no storage space left.", created="2026-04-01T10:00:00"),
    ticket("INC7", "test", "Network", "VPN", "VPN", "G1", "VPN 809 error again",
           "VPN gives error 809 again from home."),
    ticket("INC8", "train", "Network", "Remote Desktop", "Citrix", "G7", "Remote desktop connection drops",
           "The Citrix remote session connection drops every hour."),
]

# Words that mean the same thing share a dimension, so a paraphrase gets a high cosine (like a real model)
# while a ticket that only shares the word "vpn" does not.
CONCEPTS = {
    0: {"vpn", "tunnel", "remote", "remotely", "connect", "connecting", "connection", "establish", "citrix"},
    1: {"809"},
    2: {"password", "expired", "sign", "prompts", "asking", "login"},
    3: {"printer", "print", "paper", "jam"},
    4: {"outlook", "mailbox", "email", "mail", "receive"},
    5: {"disk", "storage", "full", "space"},
    6: {"home"},
    7: {"laptop"},
}
DIM = {word: d for d, words in CONCEPTS.items() for word in words}


class ConceptEmbed:
    def __call__(self, texts):
        out = np.zeros((len(texts), len(CONCEPTS)), dtype=np.float32)
        for row, text in enumerate(texts):
            for token in tokenize(text):
                if token in DIM:
                    out[row, DIM[token]] += 1.0
        return out


PARAPHRASE = "Remote tunnel will not establish at home, code 809"  # INC1 in other words


@pytest.fixture(scope="module")
def index():
    return build_ticket_index(ROWS, embed=ConceptEmbed())


def ids(hits):
    return [h.ticket_id for h in hits]


def test_paraphrased_duplicate_ranks_first(index):
    assert index.mode == "hybrid"
    top = search_similar(PARAPHRASE, index=index)[0]
    assert top.ticket_id == "INC1" and top.match == "likely_duplicate"
    assert "shared error code: 809" in top.why and any(w.startswith("similar meaning") for w in top.why)
    assert "linked to problem: PRB-12" in top.why and top.problem_id == "PRB-12"


def test_filters_exclude_before_ranking(index):
    hits = search_similar(PARAPHRASE, k=10, filters=TicketFilters(category="hardware"), index=index)
    assert set(ids(hits)) == {"INC4", "INC6"}  # case-insensitive, nothing else gets in
    dated = search_similar(PARAPHRASE, k=10, index=index,
                           filters=TicketFilters(date_from="2026-02-15T00:00:00", date_to="2026-03-31T00:00:00"))
    assert ids(dated) == ["INC2"]


def test_service_boost_changes_order(index):
    plain = search_similar(PARAPHRASE, k=8, index=index)
    second = plain[1]
    service = next(r["affected_service"] for r in ROWS if r["ticket_id"] == second.ticket_id)
    assert service != "VPN"
    boosted = search_similar(PARAPHRASE, k=8, affected_service=service, index=index)
    assert boosted[0].ticket_id == second.ticket_id
    assert f"same affected_service: {service}" in boosted[0].why
    assert boosted[0].score == second.score  # the bonus changes the order, not the shown similarity


def test_keyword_only_lookalike_is_flagged(index):
    hits = {h.ticket_id: h for h in search_similar("VPN error 809 at home", k=8, index=index)}
    assert hits["INC3"].lookalike and "keyword overlap only" in hits["INC3"].why  # shares "vpn", other issue
    assert hits["INC3"].match == "weak"
    assert not hits["INC1"].lookalike
    hinted = {h.ticket_id: h for h in search_similar("VPN error 809 at home", k=8, category_hint="Hardware",
                                                     index=index)}
    assert hinted["INC1"].lookalike and "different category: Network" in hinted["INC1"].why


def test_bm25_only_fallback():
    idx = build_ticket_index(ROWS)  # conftest sets DOC_SEARCH_DENSE=0
    assert idx.mode == "bm25"
    hits = search_similar("VPN error 809 at home", index=idx)
    assert hits[0].ticket_id == "INC1" and 0.0 < hits[0].score <= 1.0  # Jaccard of word sets
    assert not any(w.startswith("similar meaning") for w in hits[0].why)
    with pytest.raises(RuntimeError):
        search_similar("x", method="hybrid", index=idx)


def test_failing_embedder_falls_back_to_bm25():
    def broken(texts):
        raise OSError("model not downloaded")
    idx = build_ticket_index(ROWS, embed=broken)
    assert idx.mode == "bm25" and search_similar("VPN 809", index=idx)


def test_test_split_is_never_indexed(index):
    assert "INC7" not in {r["ticket_id"] for r in index.rows}
    assert {r["split"] for r in index.rows} == {"train", "val"}
    assert "INC7" not in ids(search_similar("VPN 809 error again", k=10, index=index))


def test_empty_index_and_missing_csv_return_nothing(monkeypatch, tmp_path):
    assert search_similar("anything", index=build_ticket_index([])) == []
    monkeypatch.setenv("TICKETS_CSV", str(tmp_path / "missing.csv"))
    similar.get_ticket_index.cache_clear()
    try:
        assert search_similar("anything") == []
    finally:
        similar.get_ticket_index.cache_clear()


def test_resolution_text_is_masked(index):
    hit = search_similar(PARAPHRASE, k=1, index=index)[0]
    assert "jane.doe@example.com" not in hit.resolution_text
    assert hit.resolution_text == "Called <EMAIL> and reset the VPN profile. Steps: Reset VPN profile | Reconnect"
    assert all("jane.doe" not in str(v) for row in index.rows for v in row.values())


def test_retrieval_eval_on_a_toy_case(index):
    queries = [r for r in ROWS if r["split"] == "test"]
    queries.append(ticket("INC9", "test", "Network", "Wi-Fi", "Wi-Fi", "G99", "Wi-Fi down", "No Wi-Fi."))
    report = retrieval_eval(queries, index, k=1)
    for method in ("bm25", "dense", "hybrid"):
        assert report[method]["recall_at_k"] == 1.0 and report[method]["mrr"] == 1.0
        assert report[method]["n"] == 1  # INC9's group has nothing in the index: skipped
        assert report[method]["lookalike_rate_top3"] == 0.0
    bm25_only = retrieval_eval(queries, build_ticket_index(ROWS), k=1)
    assert bm25_only["dense"] == {"skipped": "no embeddings"} and bm25_only["bm25"]["recall_at_k"] == 1.0
