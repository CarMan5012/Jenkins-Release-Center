import pytest
from fastapi import HTTPException
from app.core.config import settings
import app.main as main_module

def test_base_path_routing_with_jenkins(monkeypatch, tmp_path):
    # Set BASE_PATH to /jenkins
    monkeypatch.setattr(settings, "BASE_PATH", "/jenkins")
    
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    index_file = dist_dir / "index.html"
    index_file.write_text("<html>Jenkins App</html>")
    
    asset_file = dist_dir / "assets" / "app.js"
    asset_file.parent.mkdir()
    asset_file.write_text("console.log('app');")
    
    monkeypatch.setattr(main_module, "dist_path", str(dist_dir))
    
    # 1. Non-matching base paths should raise HTTPException 404
    with pytest.raises(HTTPException) as exc_info:
        main_module.spa_fallback("")
    assert exc_info.value.status_code == 404
    
    with pytest.raises(HTTPException) as exc_info:
        main_module.spa_fallback("dashboard")
    assert exc_info.value.status_code == 404

    # 2. Matching base path routes should return FileResponse for index.html or assets
    res_jenkins = main_module.spa_fallback("jenkins")
    assert res_jenkins.path == str(index_file)

    res_jenkins_slash = main_module.spa_fallback("jenkins/")
    assert res_jenkins_slash.path == str(index_file)

    res_jenkins_sub = main_module.spa_fallback("jenkins/dashboard")
    assert res_jenkins_sub.path == str(index_file)

    res_asset = main_module.spa_fallback("jenkins/assets/app.js")
    assert res_asset.path == str(asset_file)

def test_base_path_routing_with_root(monkeypatch, tmp_path):
    # Set BASE_PATH to /
    monkeypatch.setattr(settings, "BASE_PATH", "/")
    
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    index_file = dist_dir / "index.html"
    index_file.write_text("<html>Root App</html>")
    
    monkeypatch.setattr(main_module, "dist_path", str(dist_dir))
    
    # With BASE_PATH=/, root and sub-routes should all return index.html
    res_root = main_module.spa_fallback("")
    assert res_root.path == str(index_file)
    
    res_dash = main_module.spa_fallback("dashboard")
    assert res_dash.path == str(index_file)
