from pathlib import Path
from core.result_parser import parse_log

def test_parse_log_combines_dc_and_tran(tmp_path):
    log = tmp_path / "test.log"
    log.write_text(
        "vm: v(out)=0.9 at 0.9\n"
        "tphl=5e-12 FROM 1e-9 TO 1.005e-9\n"
        "tplh=7e-12 FROM 2e-9 TO 2.007e-9\n"
    )
    metrics = parse_log(log)
    assert metrics["vm"] == 0.9
    assert metrics["tphl"] == 5e-12
    assert metrics["tplh"] == 7e-12
    assert metrics["tpd"] == 6e-12
