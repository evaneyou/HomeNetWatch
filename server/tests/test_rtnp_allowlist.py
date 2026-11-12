"""The UDP 8998 filter: rewritten only when the peers change, and then in one transaction."""

from __future__ import annotations

from app.rtnp_allowlist import ACCEPT_RULE, is_current, jump_commands, nft_script, set_addresses

SET_TWO = """table ip filter {
	set rtnp_sources {
		type ipv4_addr
		elements = { 203.0.113.10,
			     198.51.100.7 }
	}
}
"""
SET_EMPTY = """table ip filter {
	set rtnp_sources {
		type ipv4_addr
	}
}
"""
CHAIN_ALLOW = "table ip filter {\n\tchain RTNP_IN {\n\t\tip saddr @rtnp_sources accept\n\t\tdrop\n\t}\n}\n"
CHAIN_DROP = "table ip filter {\n\tchain RTNP_IN {\n\t\tdrop\n\t}\n}\n"
CHAIN6 = "table ip6 filter {\n\tchain RTNP_IN {\n\t\tdrop\n\t}\n}\n"


def test_the_addresses_are_read_from_a_set_listing_that_wraps():
    assert set_addresses(SET_TWO) == {"203.0.113.10", "198.51.100.7"}
    assert set_addresses(SET_EMPTY) == set()
    assert set_addresses("") == set()


def test_an_unchanged_peer_list_needs_no_rewrite():
    # Order does not matter: WireGuard and nft list the same peers in different orders.
    assert is_current(["198.51.100.7", "203.0.113.10"], SET_TWO, CHAIN_ALLOW, CHAIN6)
    assert is_current([], SET_EMPTY, CHAIN_DROP, CHAIN6)


def test_a_changed_or_missing_filter_is_rewritten():
    assert not is_current(["203.0.113.10"], SET_TWO, CHAIN_ALLOW, CHAIN6), "a peer left"
    assert not is_current(["203.0.113.10", "198.51.100.7", "192.0.2.1"], SET_TWO, CHAIN_ALLOW, CHAIN6), "a peer joined"
    assert not is_current(["203.0.113.10", "198.51.100.7"], SET_TWO, CHAIN_DROP, CHAIN6), "the accept rule is gone"
    assert not is_current([], SET_EMPTY, CHAIN_ALLOW, CHAIN6), "no peers, but the chain still accepts"
    assert not is_current(["203.0.113.10", "198.51.100.7"], SET_TWO, "", CHAIN6), "first run: no chain yet"
    assert not is_current(["203.0.113.10", "198.51.100.7"], SET_TWO, CHAIN_ALLOW, ""), "no IPv6 drop"


def test_the_rewrite_is_one_file_that_ends_in_drop():
    script = nft_script(["203.0.113.10", "198.51.100.7"])
    lines = script.splitlines()
    assert "flush set ip filter rtnp_sources" in lines
    assert "add element ip filter rtnp_sources { 203.0.113.10, 198.51.100.7 }" in lines
    # The set is filled before the accept rule that reads it, and drop is last.
    assert lines.index("add element ip filter rtnp_sources { 203.0.113.10, 198.51.100.7 }") < lines.index(f"add rule ip filter RTNP_IN {ACCEPT_RULE}")
    assert lines[-1] == "add rule ip filter RTNP_IN drop"
    assert "add rule ip6 filter RTNP_IN drop" in lines


def test_no_peers_means_drop_everything():
    lines = nft_script([]).splitlines()
    assert not any("accept" in line or "add element" in line for line in lines)
    assert lines[-1] == "add rule ip filter RTNP_IN drop"


def test_the_jump_is_added_once():
    first = [" ".join(command) for command in jump_commands("", "")]
    assert sum("jump RTNP_IN" in command for command in first) == 2
    again = [" ".join(command) for command in jump_commands("jump RTNP_IN", "jump RTNP_IN")]
    assert not any("jump RTNP_IN" in command for command in again)
    assert any(command.startswith("iptables -D DOCKER-USER") for command in again)
