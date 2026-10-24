"""SOH 추정 모듈 테스트"""
import pytest
from app.modules.scanner.soh_estimation import (
    estimate_soh_by_throughput,
    estimate_soh_by_time,
    estimate_soh_by_energy,
    estimate_soh_multimethod,
    format_soh_result,
    get_battery_spec,
    SOHEstimate
)


def test_estimate_soh_by_throughput_nmc():
    """NMC 배터리 Coulomb Counting 테스트"""
    result = estimate_soh_by_throughput(
        cum_charge_ah=10000,
        cum_discharge_ah=9500,
        nominal_capacity_ah=170,
        battery_chemistry="NMC"
    )
    
    assert result.method == "coulomb_counting"
    assert 97.0 <= result.soh <= 100.0
    assert result.confidence == 0.85


def test_estimate_soh_by_throughput_lfp():
    """LFP 배터리는 노화가 더 느림"""
    result = estimate_soh_by_throughput(
        cum_charge_ah=100000,
        cum_discharge_ah=95000,
        nominal_capacity_ah=170,
        battery_chemistry="LFP"
    )
    
    # LFP는 노화계수 0.0001 (NMC 0.00015보다 낮음)
    assert result.method == "coulomb_counting"
    assert 97.0 <= result.soh <= 100.0


def test_estimate_soh_by_time_light_usage():
    """시간 기반 - 적은 사용 (연간 100 사이클)"""
    result = estimate_soh_by_time(
        operating_time_s=1 * 365.25 * 24 * 3600,  # 1년
        cum_charge_kwh=6400,  # 64kWh * 100 사이클
        nominal_capacity_kwh=64.0
    )
    
    # 1년 * 2.5% * 약 1.0 (적은 사용) = ~2.5% 감소 → 97.5%
    assert result.method == "time_based_fallback"
    assert 96.0 <= result.soh <= 98.0
    assert result.confidence == 0.6


def test_estimate_soh_by_time_heavy_usage():
    """시간 기반 - 많은 사용 (연간 500 사이클)"""
    result = estimate_soh_by_time(
        operating_time_s=2 * 365.25 * 24 * 3600,  # 2년
        cum_charge_kwh=64000,  # 64kWh * 1000 사이클 (500/년)
        nominal_capacity_kwh=64.0
    )
    
    # 2년 * 2.5% * 1.3 (많은 사용) ≈ 6.5% 감소 → 93.5%
    assert result.method == "time_based_fallback"
    assert 92.0 <= result.soh <= 95.0


def test_estimate_soh_by_energy():
    """에너지 기반 SOH 추정"""
    result = estimate_soh_by_energy(
        cum_charge_kwh=3000,
        cum_discharge_kwh=2850,
        nominal_capacity_kwh=64.0,
        battery_chemistry="NMC"
    )
    
    assert result.method == "energy_throughput"
    assert 98.0 <= result.soh <= 100.0
    assert result.confidence == 0.7


def test_estimate_soh_multimethod_with_all_data():
    """모든 데이터가 있을 때 Coulomb Counting 우선"""
    signals = {
        "cum_charge_ah": 10000,
        "cum_discharge_ah": 9500,
        "cum_charge_kwh": 3700,
        "cum_discharge_kwh": 3515,
        "operating_time_s": 1 * 365.25 * 24 * 3600
    }
    car = {"diag_model": "KONA", "diag_submodel": "ELECTRIC"}
    
    result = estimate_soh_multimethod(signals, car)
    
    # Coulomb Counting이 최우선
    assert result.method == "coulomb_counting"
    assert result.soh is not None
    assert result.confidence == 0.85


def test_estimate_soh_multimethod_without_ah():
    """Ah 데이터 없을 때 kWh 사용"""
    signals = {
        "cum_charge_kwh": 3000,
        "cum_discharge_kwh": 2850,
        "operating_time_s": 1 * 365.25 * 24 * 3600
    }
    car = {"diag_model": "IONIQ", "diag_submodel": "5"}
    
    result = estimate_soh_multimethod(signals, car)
    
    # Energy Throughput 사용
    assert result.method == "energy_throughput"
    assert result.soh is not None
    assert result.confidence == 0.7


def test_estimate_soh_multimethod_no_data():
    """데이터가 부족할 때"""
    signals = {}
    car = None
    
    result = estimate_soh_multimethod(signals, car)
    
    assert result.soh is None
    assert result.method == "unavailable"
    assert result.confidence == 0.0


def test_get_battery_spec_hyundai():
    """현대 차량 배터리 사양"""
    car = {"diag_model": "KONA", "diag_submodel": "ELECTRIC"}
    spec = get_battery_spec(car)
    
    assert spec["capacity_kwh"] == 64.0
    assert spec["capacity_ah"] == 170
    assert spec["chemistry"] == "NMC"


def test_get_battery_spec_kia():
    """기아 차량 배터리 사양"""
    car = {"diag_model": "EV6"}
    spec = get_battery_spec(car)
    
    assert spec["capacity_kwh"] == 77.4
    assert spec["capacity_ah"] == 216
    assert spec["chemistry"] == "NMC"


def test_get_battery_spec_nissan():
    """닛산 리프 배터리 사양"""
    car = {"diag_model": "LEAF"}
    spec = get_battery_spec(car)
    
    assert spec["capacity_kwh"] == 40.0
    assert spec["capacity_ah"] == 107
    assert spec["chemistry"] == "NMC"


def test_get_battery_spec_unknown():
    """알 수 없는 차량 - 기본값"""
    car = {"diag_model": "UNKNOWN"}
    spec = get_battery_spec(car)
    
    assert spec["capacity_kwh"] == 64.0
    assert spec["capacity_ah"] == 170
    assert spec["chemistry"] == "NMC"


def test_format_soh_result_excellent():
    """SOH 포맷 - 매우 좋음"""
    estimate = SOHEstimate(
        soh=95.0,
        method="coulomb_counting",
        confidence=0.85,
        assumptions="테스트",
        details={}
    )
    
    result = format_soh_result(estimate)
    
    assert result["soh"] == 95.0
    assert result["status"] == "excellent"
    assert "매우 좋음" in result["status_text"]


def test_format_soh_result_good():
    """SOH 포맷 - 좋음"""
    estimate = SOHEstimate(
        soh=85.0,
        method="energy_throughput",
        confidence=0.7,
        assumptions="테스트",
        details={}
    )
    
    result = format_soh_result(estimate)
    
    assert result["soh"] == 85.0
    assert result["status"] == "good"
    assert "좋음" in result["status_text"]


def test_format_soh_result_fair():
    """SOH 포맷 - 보통"""
    estimate = SOHEstimate(
        soh=75.0,
        method="time_based_fallback",
        confidence=0.6,
        assumptions="테스트",
        details={}
    )
    
    result = format_soh_result(estimate)
    
    assert result["soh"] == 75.0
    assert result["status"] == "fair"
    assert "보통" in result["status_text"]


def test_format_soh_result_poor():
    """SOH 포맷 - 나쁨"""
    estimate = SOHEstimate(
        soh=65.0,
        method="time_based_fallback",
        confidence=0.5,
        assumptions="테스트",
        details={}
    )
    
    result = format_soh_result(estimate)
    
    assert result["soh"] == 65.0
    assert result["status"] == "poor"
    assert "나쁨" in result["status_text"]


def test_format_soh_result_unavailable():
    """SOH 포맷 - 추정 불가"""
    estimate = SOHEstimate(
        soh=None,
        method="unavailable",
        confidence=0.0,
        assumptions="데이터 부족",
        details={}
    )
    
    result = format_soh_result(estimate)
    
    assert result["soh"] is None
    assert result["status"] == "unavailable"


def test_soh_physical_limits():
    """SOH는 60~105% 범위로 제한"""
    # 극단적으로 많이 사용
    result = estimate_soh_by_throughput(
        cum_charge_ah=10000000,
        cum_discharge_ah=10000000,
        nominal_capacity_ah=170,
        battery_chemistry="NMC"
    )
    
    # 계산상 음수가 나와도 60% 이상으로 클리핑
    assert 60.0 <= result.soh <= 105.0


def test_soh_confidence_scores():
    """각 방법의 신뢰도 점수"""
    # Coulomb Counting: 0.85
    result1 = estimate_soh_by_throughput(5000, 4750, 170, "NMC")
    assert result1.confidence == 0.85
    
    # Energy Throughput: 0.7
    result2 = estimate_soh_by_energy(3000, 2850, 64, "NMC")
    assert result2.confidence == 0.7
    
    # Time-based: 0.6
    result3 = estimate_soh_by_time(365.25 * 24 * 3600, 6400, 64)
    assert result3.confidence == 0.6
