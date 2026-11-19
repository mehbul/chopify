"""Agent option fields for video types and formats."""
import agent_ops
import projects


def test_agent_options_accept_video_type_fields():
    options = agent_ops._options(
        '{"video_type": "gaming", "format": "gaming", "video_type_model": null}')
    assert options["video_type"] == "gaming"
    assert options["format"] == "gaming"


def test_project_state_exposes_detection_fields(tmp_path):
    state = projects.init_project(tmp_path, "video.mp4", {
        "video_type": "gaming", "format": "gaming"})
    assert state["options"]["video_type"] == "gaming"
    assert state["options"]["format"] == "gaming"
