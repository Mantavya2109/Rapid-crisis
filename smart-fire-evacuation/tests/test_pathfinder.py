"""
tests/test_pathfinder.py
------------------------
Unit tests for the BFS pathfinding logic.
Run with: pytest tests/
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathfinder import bfs_to_exit, find_all_paths


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

SIMPLE_GRAPH = {
    "ROOM_101": ["HALLWAY"],
    "ROOM_102": ["HALLWAY"],
    "HALLWAY": ["EXIT_A"],
    "EXIT_A": [],
}

BRANCH_GRAPH = {
    "ROOM_101": ["HALLWAY"],
    "HALLWAY": ["EXIT_A", "EXIT_B"],
    "EXIT_A": [],
    "EXIT_B": [],
}

BLOCKED_ALL_GRAPH = {
    "ROOM_101": ["HALLWAY"],
    "HALLWAY": ["EXIT_A"],
    "EXIT_A": [],
}

EXITS_SIMPLE = ["EXIT_A"]
EXITS_BRANCH = ["EXIT_A", "EXIT_B"]


# ─────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────

class TestBFSToExit:

    def test_direct_path_found(self):
        path = bfs_to_exit(SIMPLE_GRAPH, "ROOM_101", EXITS_SIMPLE)
        assert path == ["ROOM_101", "HALLWAY", "EXIT_A"]

    def test_start_is_exit(self):
        path = bfs_to_exit(SIMPLE_GRAPH, "EXIT_A", EXITS_SIMPLE)
        assert path == ["EXIT_A"]

    def test_blocked_hallway_no_path(self):
        path = bfs_to_exit(SIMPLE_GRAPH, "ROOM_101", EXITS_SIMPLE, blocked_nodes=["HALLWAY"])
        assert path == []

    def test_blocked_exit_no_path(self):
        path = bfs_to_exit(SIMPLE_GRAPH, "ROOM_101", EXITS_SIMPLE, blocked_nodes=["EXIT_A"])
        assert path == []

    def test_unknown_start_node(self):
        path = bfs_to_exit(SIMPLE_GRAPH, "UNKNOWN_NODE", EXITS_SIMPLE)
        assert path == []

    def test_nearest_exit_chosen(self):
        """BFS should pick shortest-hop exit."""
        path = bfs_to_exit(BRANCH_GRAPH, "ROOM_101", EXITS_BRANCH)
        # Both exits are equal distance — just ensure a valid path is returned
        assert path[0] == "ROOM_101"
        assert path[-1] in EXITS_BRANCH

    def test_alternate_exit_when_one_blocked(self):
        path = bfs_to_exit(BRANCH_GRAPH, "ROOM_101", EXITS_BRANCH, blocked_nodes=["EXIT_A"])
        assert path[-1] == "EXIT_B"

    def test_no_exits_provided(self):
        path = bfs_to_exit(SIMPLE_GRAPH, "ROOM_101", exits=[])
        assert path == []

    def test_empty_graph(self):
        path = bfs_to_exit({}, "ROOM_101", EXITS_SIMPLE)
        assert path == []


class TestFindAllPaths:

    def test_returns_multiple_paths(self):
        paths = find_all_paths(BRANCH_GRAPH, "ROOM_101", EXITS_BRANCH)
        assert len(paths) == 2

    def test_sorted_by_length(self):
        paths = find_all_paths(BRANCH_GRAPH, "ROOM_101", EXITS_BRANCH)
        lengths = [len(p) for p in paths]
        assert lengths == sorted(lengths)

    def test_blocked_reduces_paths(self):
        paths = find_all_paths(BRANCH_GRAPH, "ROOM_101", EXITS_BRANCH, blocked_nodes=["EXIT_A"])
        assert len(paths) == 1
        assert paths[0][-1] == "EXIT_B"
