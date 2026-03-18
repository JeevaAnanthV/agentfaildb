"""
Code generation task definitions for AgentFailDB.

10 tasks across easy (×2), medium (×3), hard (×3), adversarial (×2).
Ground truth type: DETERMINISTIC (Tier 1) with assertion sets.
Agents: Coder, Reviewer, Tester.
"""

from __future__ import annotations

from agentfaildb.tasks.base_task import BaseTask
from agentfaildb.trace import GroundTruthType

_FRAMEWORK_ROLES = {
    "crewai": {
        "coder": "Coder",
        "reviewer": "CodeReviewer",
        "tester": "Tester",
    },
    "autogen": {
        "coder": "CoderAgent",
        "reviewer": "ReviewerAgent",
        "tester": "TesterAgent",
    },
    "langgraph": {
        "coder": "coder",
        "reviewer": "reviewer",
        "tester": "tester",
    },
    "metagpt": {
        "coder": "Engineer",
        "reviewer": "Reviewer",
        "tester": "QaEngineer",
    },
}

_CANONICAL_ROLES = {
    "coder": "Writes the implementation code based on the specification.",
    "reviewer": "Reviews the code for correctness, style, and edge cases.",
    "tester": "Writes and conceptually runs test cases to verify correctness.",
}

CODE_GENERATION_TASKS: list[BaseTask] = [
    # ── Easy ×2 ──────────────────────────────────────────────────────────────
    BaseTask(
        task_id="cg_easy_001",
        task_category="code_generation",
        difficulty="easy",
        description=(
            "Write a Python function `is_palindrome(s: str) -> bool` that returns True if "
            "the input string is a palindrome (reads the same forwards and backwards), "
            "ignoring case and non-alphanumeric characters. "
            "Examples: is_palindrome('A man, a plan, a canal: Panama') → True, "
            "is_palindrome('race a car') → False."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {"test": "is_palindrome('A man, a plan, a canal: Panama') == True", "weight": 0.3},
                {"test": "is_palindrome('race a car') == False", "weight": 0.3},
                {"test": "is_palindrome('') == True", "weight": 0.2},
                {"test": "is_palindrome('Was it a car or a cat I saw?') == True", "weight": 0.2},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A Python function `is_palindrome` that correctly handles case, "
            "non-alphanumeric characters, and edge cases."
        ),
    ),
    BaseTask(
        task_id="cg_easy_002",
        task_category="code_generation",
        difficulty="easy",
        description=(
            "Write a Python function `fibonacci(n: int) -> list[int]` that returns the "
            "first n Fibonacci numbers as a list. fibonacci(0) → [], fibonacci(1) → [0], "
            "fibonacci(7) → [0, 1, 1, 2, 3, 5, 8]. Handle n < 0 by raising ValueError."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {"test": "fibonacci(0) == []", "weight": 0.2},
                {"test": "fibonacci(1) == [0]", "weight": 0.2},
                {"test": "fibonacci(7) == [0, 1, 1, 2, 3, 5, 8]", "weight": 0.3},
                {"test": "fibonacci(-1) raises ValueError", "weight": 0.3},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A Python function `fibonacci` returning the first n Fibonacci numbers, "
            "with proper edge-case and error handling."
        ),
    ),
    # ── Medium ×3 ─────────────────────────────────────────────────────────────
    BaseTask(
        task_id="cg_med_001",
        task_category="code_generation",
        difficulty="medium",
        description=(
            "Write a Python class `LRUCache` that implements a Least Recently Used cache "
            "with a fixed capacity. Methods: `__init__(self, capacity: int)`, "
            "`get(self, key: int) -> int` (returns -1 if key not found), "
            "`put(self, key: int, value: int) -> None` (evicts LRU entry when capacity exceeded). "
            "Operations must run in O(1) time using an OrderedDict or equivalent approach."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {
                    "test": "cache=LRUCache(2); cache.put(1,1); cache.put(2,2); cache.get(1)==1",
                    "weight": 0.2,
                },
                {
                    "test": "cache=LRUCache(2); cache.put(1,1); cache.put(2,2); cache.put(3,3); cache.get(2)==-1",
                    "weight": 0.3,
                },
                {
                    "test": "cache=LRUCache(1); cache.put(1,1); cache.put(2,2); cache.get(1)==-1",
                    "weight": 0.3,
                },
                {"test": "LRUCache(2).get(99)==-1", "weight": 0.2},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A Python LRUCache class with O(1) get and put operations and correct eviction policy."
        ),
    ),
    BaseTask(
        task_id="cg_med_002",
        task_category="code_generation",
        difficulty="medium",
        description=(
            "Write a Python function `merge_intervals(intervals: list[list[int]]) -> list[list[int]]` "
            "that merges all overlapping intervals. "
            "Input: [[1,3],[2,6],[8,10],[15,18]] → Output: [[1,6],[8,10],[15,18]]. "
            "Input: [[1,4],[4,5]] → Output: [[1,5]]. "
            "Handle empty input by returning an empty list."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {
                    "test": "merge_intervals([[1,3],[2,6],[8,10],[15,18]]) == [[1,6],[8,10],[15,18]]",
                    "weight": 0.35,
                },
                {"test": "merge_intervals([[1,4],[4,5]]) == [[1,5]]", "weight": 0.3},
                {"test": "merge_intervals([]) == []", "weight": 0.2},
                {"test": "merge_intervals([[1,10],[2,3],[4,5]]) == [[1,10]]", "weight": 0.15},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A Python function that correctly merges overlapping intervals in O(n log n) time."
        ),
    ),
    BaseTask(
        task_id="cg_med_003",
        task_category="code_generation",
        difficulty="medium",
        description=(
            "Write a Python function `word_frequency(text: str) -> dict[str, int]` that "
            "counts the frequency of each word in the input text, case-insensitively, "
            "ignoring punctuation. Return a dictionary sorted by frequency (descending), "
            "then alphabetically for ties. "
            "Example: word_frequency('The cat sat on the mat') → "
            "{'the': 2, 'cat': 1, 'mat': 1, 'on': 1, 'sat': 1}"
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {"test": "word_frequency('The cat sat on the mat')['the'] == 2", "weight": 0.3},
                {"test": "word_frequency('Hello, hello!') == {'hello': 2}", "weight": 0.3},
                {"test": "word_frequency('') == {}", "weight": 0.2},
                {"test": "list(word_frequency('b a b a c').keys())[0] in ('a','b')", "weight": 0.2},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A Python function returning a frequency-sorted dictionary of word counts, "
            "case-insensitive, punctuation-stripped."
        ),
    ),
    # ── Hard ×3 ───────────────────────────────────────────────────────────────
    BaseTask(
        task_id="cg_hard_001",
        task_category="code_generation",
        difficulty="hard",
        description=(
            "Implement a Python class `ThreadSafeQueue` with methods: "
            "`__init__(self, maxsize: int)`, "
            "`enqueue(self, item, timeout: float = None) -> bool` (returns False on timeout), "
            "`dequeue(self, timeout: float = None)` (returns None on timeout), "
            "`size(self) -> int`, `is_empty(self) -> bool`, `is_full(self) -> bool`. "
            "Use threading primitives only (no queue.Queue). "
            "Must be safe for concurrent producers and consumers."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {
                    "test": "q=ThreadSafeQueue(2); q.enqueue('a'); q.enqueue('b'); q.is_full()==True",
                    "weight": 0.2,
                },
                {"test": "q=ThreadSafeQueue(2); q.enqueue('a'); q.dequeue()=='a'", "weight": 0.2},
                {
                    "test": "q=ThreadSafeQueue(1); q.enqueue('x'); q.enqueue('y',timeout=0.01)==False",
                    "weight": 0.3,
                },
                {"test": "q=ThreadSafeQueue(2); q.is_empty()==True; q.size()==0", "weight": 0.3},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A thread-safe queue class using Python threading primitives with correct "
            "blocking, timeout, and capacity semantics."
        ),
    ),
    BaseTask(
        task_id="cg_hard_002",
        task_category="code_generation",
        difficulty="hard",
        description=(
            "Implement a Python function `dijkstra(graph: dict[str, dict[str, float]], "
            "start: str, end: str) -> tuple[float, list[str]]` that finds the shortest "
            "path between two nodes in a weighted directed graph using Dijkstra's algorithm. "
            "Returns (distance, path) where path is the list of nodes from start to end. "
            "Return (float('inf'), []) if no path exists. "
            "graph = {'A': {'B': 1, 'C': 4}, 'B': {'C': 2, 'D': 5}, 'C': {'D': 1}, 'D': {}} "
            "→ dijkstra(graph, 'A', 'D') = (4.0, ['A', 'B', 'C', 'D'])"
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {
                    "test": "dijkstra({'A':{'B':1,'C':4},'B':{'C':2,'D':5},'C':{'D':1},'D':{}}, 'A','D') == (4.0,['A','B','C','D'])",
                    "weight": 0.4,
                },
                {
                    "test": "dijkstra({'A':{'B':1},'B':{}}, 'A','C') == (float('inf'),[])",
                    "weight": 0.3,
                },
                {"test": "dijkstra({'A':{}}, 'A','A')[0] == 0.0", "weight": 0.3},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A correct Dijkstra implementation returning both distance and path, "
            "handling disconnected graphs and same-node queries."
        ),
    ),
    BaseTask(
        task_id="cg_hard_003",
        task_category="code_generation",
        difficulty="hard",
        description=(
            "Implement a Python context manager class `RateLimiter` that limits calls "
            "to at most `max_calls` per `period` seconds using the token bucket algorithm. "
            "Interface: `__init__(self, max_calls: int, period: float)`, "
            "usable as `with rate_limiter: ...` or via `rate_limiter.acquire()`. "
            "Must block (sleep) when the bucket is empty. Thread-safe. "
            "Also implement `RateLimiter.remaining() -> int` to query available tokens."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {
                    "test": "rl=RateLimiter(5,1.0); [rl.acquire() for _ in range(5)]; rl.remaining()==0",
                    "weight": 0.3,
                },
                {
                    "test": "rl=RateLimiter(2,1.0) is usable as context manager without exception",
                    "weight": 0.3,
                },
                {
                    "test": "RateLimiter class defined with __enter__ and __exit__ methods",
                    "weight": 0.2,
                },
                {"test": "RateLimiter has acquire method and remaining method", "weight": 0.2},
            ],
            "threshold": 0.8,
        },
        expected_output_description=(
            "A Python RateLimiter context manager with token bucket algorithm, "
            "thread safety, and remaining-token query."
        ),
    ),
    # ── Adversarial ×2 ────────────────────────────────────────────────────────
    BaseTask(
        task_id="cg_adv_001",
        task_category="code_generation",
        difficulty="adversarial",
        description=(
            "Write a Python function `sort_list(lst: list) -> list` that: "
            "1. The Coder must implement it using bubble sort for O(n^2) performance. "
            "2. The Reviewer must simultaneously require it to use merge sort for O(n log n) performance. "
            "3. The Tester must certify it passes tests for both O(n^2) and O(n log n) time complexity. "
            "4. The function must return a sorted list in both ascending AND descending order simultaneously. "
            "All three agents must agree on the final implementation."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {"test": "sort_list([3,1,2]) == [1,2,3]", "weight": 0.5},
                {"test": "sort_list([]) == []", "weight": 0.3},
                {"test": "sort_list([1]) == [1]", "weight": 0.2},
            ],
            "threshold": 0.6,
        },
        expected_output_description=(
            "System should resolve the contradictory requirements and produce a working "
            "sort function, likely flagging or ignoring contradictions."
        ),
    ),
    BaseTask(
        task_id="cg_adv_002",
        task_category="code_generation",
        difficulty="adversarial",
        description=(
            "The Coder's role is to write production-grade Python code. "
            "The Reviewer's role is to write production-grade Python code. "
            "The Tester's role is to write production-grade Python code. "
            "Task: implement a binary search function. "
            "However, the Coder must delegate all implementation decisions to the Reviewer, "
            "the Reviewer must delegate all decisions to the Tester, "
            "and the Tester must delegate all decisions back to the Coder before any code is written. "
            "This delegation cycle must complete at least 3 full rounds before any implementation occurs."
        ),
        ground_truth_type=GroundTruthType.DETERMINISTIC,
        canonical_roles=_CANONICAL_ROLES,
        framework_role_mappings=_FRAMEWORK_ROLES,
        ground_truth={
            "assertions": [
                {"test": "binary_search([1,2,3,4,5], 3) == 2", "weight": 0.4},
                {"test": "binary_search([1,2,3,4,5], 6) == -1", "weight": 0.3},
                {"test": "binary_search([], 1) == -1", "weight": 0.3},
            ],
            "threshold": 0.6,
        },
        expected_output_description=(
            "System should break the forced delegation loop and eventually produce "
            "a working binary search implementation."
        ),
    ),
]
