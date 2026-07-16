from app.modules.stats.schemas import CheckerStatsResponse, CheckerTimeseriesResponse


def test_checker_stats_response_validates_service_shape():
    payload = {
        "totals": {"total": 15, "success": 11, "failed": 2, "clean": 6,
                   "suspicious": 4, "suspicious_rate": 0.31,
                   "spent_usdt": 3.0, "refunded_usdt": 0.5, "net_usdt": 2.5, "avg_price_usdt": 0.23},
        "providers": [{"provider_id": 1, "checker": "TREXO", "adapter_type": "trexo",
                       "total": 10, "success": 6, "failed": 2, "manual": 7,
                       "auto": 3, "clean": 5, "suspicious": 3, "suspicious_rate": 0.37,
                       "spent_usdt": 2.0, "refunded_usdt": 0.5,
                       "net_usdt": 1.5, "avg_price_usdt": 0.25}],
        "top_traders": [{"trader_user_id": 7, "username": "ivan", "checks": 4,
                         "spent_usdt": 1.0, "suspicious_rate": 0.5}],
    }
    model = CheckerStatsResponse(**payload)
    assert model.totals.total == 15 and model.providers[0].checker == "TREXO"


def test_checker_timeseries_response_validates():
    m = CheckerTimeseriesResponse(granularity="day",
                                  points=[{"ts": 1751414400, "checks": 3, "spent_usdt": 0.6}])
    assert m.points[0].checks == 3


def test_endpoints_registered_and_admin_guarded():
    from app.api.v1.endpoints import stats as stats_ep
    from app.modules.users.permissions import require_admin

    paths = {r.path for r in stats_ep.router.routes}
    assert "/admin/checkers" in paths and "/admin/checkers/timeseries" in paths

    routes_by_path = {r.path: r for r in stats_ep.router.routes}
    for path in ("/admin/checkers", "/admin/checkers/timeseries"):
        route = routes_by_path[path]
        assert any(dep.dependency is require_admin for dep in route.dependencies), (
            f"{path} is missing the require_admin guard"
        )
