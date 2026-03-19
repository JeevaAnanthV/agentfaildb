"""
Tests for the task registry and task definition validity.
"""

from __future__ import annotations


from agentfaildb.tasks import ALL_TASKS, get_tasks_by_category, get_tasks_by_difficulty
from agentfaildb.trace import GroundTruthType

_REQUIRED_FRAMEWORKS = {"crewai", "autogen", "langgraph"}
_VALID_DIFFICULTIES = {"easy", "medium", "hard", "adversarial"}
_VALID_CATEGORIES = {
    "collaborative_research",
    "code_generation",
    "debate_reasoning",
    "planning",
    "data_analysis",
}


class TestAllTasksCount:
    def test_all_tasks_has_250_items(self) -> None:
        """ALL_TASKS must contain exactly 250 tasks (50 per category × 5 categories)."""
        assert len(ALL_TASKS) == 250

    def test_each_category_has_50_tasks(self) -> None:
        for category in _VALID_CATEGORIES:
            tasks = get_tasks_by_category(category)
            assert len(tasks) == 50, f"Category '{category}' has {len(tasks)} tasks, expected 50"

    def test_difficulty_distribution_per_category(self) -> None:
        """Each category must have 10 easy, 15 medium, 15 hard, 10 adversarial."""
        expected = {"easy": 10, "medium": 15, "hard": 15, "adversarial": 10}
        for category in _VALID_CATEGORIES:
            tasks = get_tasks_by_category(category)
            by_diff: dict[str, int] = {}
            for task in tasks:
                by_diff[task.difficulty] = by_diff.get(task.difficulty, 0) + 1
            for diff, count in expected.items():
                assert by_diff.get(diff, 0) == count, (
                    f"Category '{category}' has {by_diff.get(diff, 0)} {diff} tasks, expected {count}"
                )


class TestTaskIds:
    def test_all_task_ids_unique(self) -> None:
        ids = [t.task_id for t in ALL_TASKS]
        assert len(ids) == len(set(ids)), "Duplicate task_ids found"

    def test_task_ids_non_empty(self) -> None:
        for task in ALL_TASKS:
            assert task.task_id, f"Task has empty task_id: {task}"


class TestGroundTruthFormat:
    def test_tier1_ground_truth_format(self) -> None:
        """DETERMINISTIC tasks must have assertions list with threshold."""
        for task in ALL_TASKS:
            if task.ground_truth_type == GroundTruthType.DETERMINISTIC:
                assert task.ground_truth is not None, f"{task.task_id} has no ground_truth"
                gt = task.ground_truth
                assert "assertions" in gt, f"{task.task_id} missing 'assertions' key"
                assert "threshold" in gt, f"{task.task_id} missing 'threshold' key"
                assert isinstance(gt["assertions"], list), f"{task.task_id} assertions not a list"
                assert len(gt["assertions"]) > 0, f"{task.task_id} has empty assertions"
                for assertion in gt["assertions"]:
                    assert "test" in assertion, f"{task.task_id} assertion missing 'test'"
                    assert "weight" in assertion, f"{task.task_id} assertion missing 'weight'"

    def test_tier2_ground_truth_format(self) -> None:
        """CLAIM_LIST tasks must have claims list with threshold."""
        for task in ALL_TASKS:
            if task.ground_truth_type == GroundTruthType.CLAIM_LIST:
                assert task.ground_truth is not None, f"{task.task_id} has no ground_truth"
                gt = task.ground_truth
                assert "claims" in gt, f"{task.task_id} missing 'claims' key"
                assert "threshold" in gt, f"{task.task_id} missing 'threshold' key"
                assert isinstance(gt["claims"], list), f"{task.task_id} claims not a list"
                assert len(gt["claims"]) > 0, f"{task.task_id} has empty claims"
                for claim in gt["claims"]:
                    assert "claim" in claim, f"{task.task_id} claim item missing 'claim'"
                    assert "weight" in claim, f"{task.task_id} claim item missing 'weight'"

    def test_tier3_ground_truth_format(self) -> None:
        """RUBRIC tasks must have dimensions list with threshold."""
        for task in ALL_TASKS:
            if task.ground_truth_type == GroundTruthType.RUBRIC:
                assert task.ground_truth is not None, f"{task.task_id} has no ground_truth"
                gt = task.ground_truth
                assert "dimensions" in gt, f"{task.task_id} missing 'dimensions' key"
                assert "threshold" in gt, f"{task.task_id} missing 'threshold' key"
                assert isinstance(gt["dimensions"], list), f"{task.task_id} dimensions not a list"
                assert len(gt["dimensions"]) > 0, f"{task.task_id} has empty dimensions"


class TestCanonicalRoles:
    def test_canonical_roles_non_empty(self) -> None:
        for task in ALL_TASKS:
            assert task.canonical_roles, f"{task.task_id} has empty canonical_roles"

    def test_canonical_roles_values_are_strings(self) -> None:
        for task in ALL_TASKS:
            for role, desc in task.canonical_roles.items():
                assert isinstance(role, str), f"{task.task_id} role key not a string"
                assert isinstance(desc, str) and desc, (
                    f"{task.task_id} role '{role}' has empty description"
                )


class TestFrameworkRoleMappings:
    def test_all_four_frameworks_present(self) -> None:
        for task in ALL_TASKS:
            mappings = task.framework_role_mappings
            for fw in _REQUIRED_FRAMEWORKS:
                assert fw in mappings, (
                    f"{task.task_id} missing framework '{fw}' in framework_role_mappings"
                )

    def test_mappings_cover_all_canonical_roles(self) -> None:
        for task in ALL_TASKS:
            for fw in _REQUIRED_FRAMEWORKS:
                fw_mapping = task.framework_role_mappings.get(fw, {})
                for canonical_role in task.canonical_roles:
                    assert canonical_role in fw_mapping, (
                        f"{task.task_id}: canonical role '{canonical_role}' "
                        f"not mapped in framework '{fw}'"
                    )

    def test_mapped_role_names_non_empty(self) -> None:
        for task in ALL_TASKS:
            for fw, mapping in task.framework_role_mappings.items():
                for canonical, fw_name in mapping.items():
                    assert fw_name, (
                        f"{task.task_id}: framework '{fw}' role '{canonical}' maps to empty string"
                    )


class TestHelperFunctions:
    def test_get_tasks_by_category_returns_correct_subset(self) -> None:
        for category in _VALID_CATEGORIES:
            tasks = get_tasks_by_category(category)
            assert all(t.task_category == category for t in tasks)

    def test_get_tasks_by_difficulty_returns_correct_subset(self) -> None:
        for difficulty in _VALID_DIFFICULTIES:
            tasks = get_tasks_by_difficulty(difficulty)
            assert all(t.difficulty == difficulty for t in tasks)

    def test_get_tasks_by_unknown_category_returns_empty(self) -> None:
        tasks = get_tasks_by_category("nonexistent_category")
        assert tasks == []
