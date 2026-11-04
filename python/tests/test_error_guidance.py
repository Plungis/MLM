from mlm.error_guidance import error_guidance


def test_error_guidance_classifies_known_services() -> None:
    wedge = error_guidance("wedge reserve reached (3 available, 3 reserved)")
    auth = error_guidance("Client error '403 Forbidden'")
    qbit = error_guidance("qBittorrent rejected the request")
    path = error_guidance("check the path_mapping configuration")
    unknown = error_guidance("something novel exploded")

    assert wedge["title"] == "Freeleech wedge reserve reached"
    assert auth["title"] == "MyAnonamouse session is not authorized"
    assert qbit["title"] == "qBittorrent could not accept the release"
    assert path["component"] == "organizer"
    assert unknown["title"] == "Download processing failed"
    assert len(unknown["steps"]) == 3
