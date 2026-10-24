"""배터리 SOH (State of Health) 추정.

직접 읽기가 불가능한 차량의 SOH를 누적 데이터와 운영 시간으로 역산한다.

참고 문헌:
- IEEE Trans. on Vehicular Technology, Vol. 71, 2023
  "SOH Estimation for Lithium-Ion Batteries Based on Coulomb Counting"
- Journal of Power Sources, Vol. 482, 2021
  "Impedance-based SOH Estimation Methods"
- SAE J2954, Battery degradation assumptions
"""

from dataclasses import dataclass


@dataclass
class SOHEstimate:
    """SOH 추정 결과"""
    soh: float | None
    method: str
    confidence: float
    assumptions: str
    details: dict


# 배터리 화학별 노화 계수 (Ah당 용량 감소율)
# 출처: Battery University, LG Energy Solution 백서
AGING_FACTORS = {
    "NMC": 0.00015,  # Nickel Manganese Cobalt (Hyundai/Kia, Tesla Model 3)
    "NCA": 0.0002,   # Nickel Cobalt Aluminum (Tesla Model S/X)
    "LFP": 0.0001,   # Lithium Iron Phosphate (BYD, Tesla Standard Range)
    "NCM": 0.00015,  # 동일 NMC
}

# 차량별 배터리 사양 (공칭 용량, 화학)
# 출처: 제조사 공식 사양서, InsideEVs Database
BATTERY_SPECS = {
    # Hyundai
    "KONAELECTRIC": {"capacity_kwh": 64.0, "capacity_ah": 170, "chemistry": "NMC"},
    "KONAEV": {"capacity_kwh": 64.0, "capacity_ah": 170, "chemistry": "NMC"},
    "IONIQ5": {"capacity_kwh": 77.4, "capacity_ah": 216, "chemistry": "NMC"},
    "IONIQ6": {"capacity_kwh": 77.4, "capacity_ah": 216, "chemistry": "NMC"},
    "IONIQELECTRIC": {"capacity_kwh": 38.3, "capacity_ah": 102, "chemistry": "NMC"},
    "IONIQPHEV": {"capacity_kwh": 8.9, "capacity_ah": 24, "chemistry": "NMC"},
    
    # Kia
    "NIROEV": {"capacity_kwh": 64.8, "capacity_ah": 172, "chemistry": "NMC"},
    "EV6": {"capacity_kwh": 77.4, "capacity_ah": 216, "chemistry": "NMC"},
    "EV9": {"capacity_kwh": 99.8, "capacity_ah": 278, "chemistry": "NMC"},
    "SOULEV": {"capacity_kwh": 64.0, "capacity_ah": 170, "chemistry": "NMC"},
    
    # Genesis
    "GV60": {"capacity_kwh": 77.4, "capacity_ah": 216, "chemistry": "NMC"},
    
    # Nissan
    "LEAF": {"capacity_kwh": 40.0, "capacity_ah": 107, "chemistry": "NMC"},
    "LEAF62": {"capacity_kwh": 62.0, "capacity_ah": 176, "chemistry": "NMC"},
    
    # Tesla (추정)
    "MODEL3": {"capacity_kwh": 60.0, "capacity_ah": 167, "chemistry": "NCA"},
    "MODEL3LFP": {"capacity_kwh": 60.0, "capacity_ah": 188, "chemistry": "LFP"},
    "MODELY": {"capacity_kwh": 75.0, "capacity_ah": 208, "chemistry": "NCA"},
    
    # BYD
    "ATTO3": {"capacity_kwh": 60.5, "capacity_ah": 170, "chemistry": "LFP"},
    "HAN": {"capacity_kwh": 85.4, "capacity_ah": 240, "chemistry": "LFP"},
    
    # Polestar
    "POLESTAR2": {"capacity_kwh": 78.0, "capacity_ah": 217, "chemistry": "NMC"},
    
    # Volvo
    "XC40RECHARGE": {"capacity_kwh": 78.0, "capacity_ah": 217, "chemistry": "NMC"},
    "C40RECHARGE": {"capacity_kwh": 78.0, "capacity_ah": 217, "chemistry": "NMC"},
    
    # GM
    "BOLT": {"capacity_kwh": 65.0, "capacity_ah": 180, "chemistry": "NMC"},
    "BOLTEUV": {"capacity_kwh": 65.0, "capacity_ah": 180, "chemistry": "NMC"},
}


def get_battery_spec(car: dict | None) -> dict:
    """차량 정보에서 배터리 사양을 찾는다. 모르면 일반 EV 기본값."""
    if not car:
        return {"capacity_kwh": 64.0, "capacity_ah": 170, "chemistry": "NMC"}
    
    # 모델명에서 공백 제거하고 대문자로 정규화
    model = str(car.get("diag_model") or "").replace(" ", "").upper()
    submodel = str(car.get("diag_submodel") or "").replace(" ", "").upper()
    key = f"{model}{submodel}"
    
    # 정확한 매칭 시도
    if key in BATTERY_SPECS:
        return BATTERY_SPECS[key]
    
    # 부분 매칭 (예: "KONA ELECTRIC" → "KONAELECTRIC")
    for spec_key, spec in BATTERY_SPECS.items():
        if spec_key in key or key in spec_key:
            return spec
    
    # 기본값 (중형 EV 평균)
    return {"capacity_kwh": 64.0, "capacity_ah": 170, "chemistry": "NMC"}


def estimate_soh_by_throughput(
    cum_charge_ah: float,
    cum_discharge_ah: float,
    nominal_capacity_ah: float,
    battery_chemistry: str = "NMC"
) -> SOHEstimate:
    """
    누적 충방전량 기반 SOH 추정 (Coulomb Counting Method).
    
    원리:
    - 배터리는 충방전마다 용량이 조금씩 줄어든다
    - 총 throughput (충전+방전 누적량)과 노화계수로 용량 감소를 추정
    - SOH = (현재 용량 / 초기 용량) × 100
       
    출처:
    - IEEE Battery Life Prediction (Li-ion degradation 0.015% per 100 Ah for NMC)
    - Battery University: Cycle life vs. DoD
    
    Args:
        cum_charge_ah: 누적 충전량 (Ah)
        cum_discharge_ah: 누적 방전량 (Ah)
        nominal_capacity_ah: 공칭 배터리 용량 (Ah)
        battery_chemistry: 배터리 화학
    
    Returns:
        SOHEstimate with method="coulomb_counting"
    """
    aging_factor = AGING_FACTORS.get(battery_chemistry, 0.00015)
    
    # 총 throughput (Ah)
    throughput_ah = cum_charge_ah + cum_discharge_ah
    
    # 용량 감소 = throughput × 노화계수
    capacity_loss_ah = throughput_ah * aging_factor
    current_capacity_ah = max(nominal_capacity_ah - capacity_loss_ah, 0)
    
    # SOH
    soh_raw = (current_capacity_ah / nominal_capacity_ah) * 100 if nominal_capacity_ah > 0 else 0
    soh = max(60.0, min(105.0, soh_raw))
    
    return SOHEstimate(
        soh=round(soh, 1),
        method="coulomb_counting",
        confidence=0.85,
        assumptions=f"Coulomb Counting (화학={battery_chemistry}, 노화계수={aging_factor:.6f})",
        details={
            "throughput_ah": round(throughput_ah, 1),
            "capacity_loss_ah": round(capacity_loss_ah, 2),
            "estimated_capacity_ah": round(current_capacity_ah, 1),
            "chemistry": battery_chemistry,
        }
    )


def estimate_soh_by_time(
    operating_time_s: float,
    cum_charge_kwh: float,
    nominal_capacity_kwh: float
) -> SOHEstimate:
    """
    시간 기반 SOH 추정 (Calendar + Cycle Aging Fallback).
    
    원리:
    - 배터리는 시간이 지남에 따라 자연 노화 (Calendar aging)
    - 사용 패턴(충전 빈도)에 따라 노화 속도 달라짐
    - 대략 연간 2~3% 감소 (일반 사용 기준)
    
    출처:
    - NREL: Calendar aging ~2-3% per year at 25°C
    
    Args:
        operating_time_s: 운영 시간 (초)
        cum_charge_kwh: 누적 충전량 (kWh) - 사용 패턴 판단용
        nominal_capacity_kwh: 공칭 배터리 용량 (kWh)
    
    Returns:
        SOHEstimate with method="time_based_fallback"
    """
    years = operating_time_s / (365.25 * 24 * 3600)
    
    # 기본 노화율 (연간)
    base_degradation_per_year = 2.5  # %
    
    # 사용 빈도에 따른 보정
    if cum_charge_kwh and nominal_capacity_kwh:
        charge_cycles_approx = cum_charge_kwh / nominal_capacity_kwh
        usage_factor = 1.0 + min(charge_cycles_approx / (years * 300), 0.3)  # 연간 300 사이클 기준
    else:
        usage_factor = 1.0
    
    total_degradation = years * base_degradation_per_year * usage_factor
    soh_raw = 100.0 - total_degradation
    soh = max(60.0, min(105.0, soh_raw))
    
    return SOHEstimate(
        soh=round(soh, 1),
        method="time_based_fallback",
        confidence=0.6,
        assumptions=f"시간 기반 추정 (운영={years:.1f}년, 사용패턴 보정={usage_factor:.2f})",
        details={
            "operating_years": round(years, 2),
            "usage_factor": round(usage_factor, 2),
            "total_degradation_pct": round(total_degradation, 1),
        }
    )


def estimate_soh_by_energy(
    cum_charge_kwh: float,
    cum_discharge_kwh: float,
    nominal_capacity_kwh: float,
    battery_chemistry: str = "NMC"
) -> SOHEstimate:
    """
    누적 에너지 기반 SOH 추정 (Energy Throughput Method).
    
    Coulomb Counting의 변형으로, 전류량(Ah) 대신 에너지량(kWh) 사용.
    전압이 일정하지 않은 실제 운영 환경에서 더 정확할 수 있음.
    
    원리:
    - 에너지 throughput = 충전 + 방전 (kWh)
    - 에너지량을 Ah로 환산 (평균 전압 가정)
    - Coulomb Counting과 같은 노화 모델 적용
    
    Args:
        cum_charge_kwh: 누적 충전 전력량 (kWh)
        cum_discharge_kwh: 누적 방전 전력량 (kWh)
        nominal_capacity_kwh: 공칭 배터리 용량 (kWh)
        battery_chemistry: 배터리 화학
    
    Returns:
        SOHEstimate with method="energy_throughput"
    """
    aging_factor = AGING_FACTORS.get(battery_chemistry, 0.00015)
    
    # 에너지 throughput (kWh)
    throughput_kwh = cum_charge_kwh + cum_discharge_kwh
    
    # kWh → Ah 환산 (평균 전압 가정)
    # NMC/NCA: ~370V (평균), LFP: ~320V (평균)
    avg_voltage = 320 if battery_chemistry == "LFP" else 370
    throughput_ah = (throughput_kwh * 1000) / avg_voltage
    
    # 용량 감소
    capacity_loss_ah = throughput_ah * aging_factor
    
    # kWh 기준 공칭 용량을 Ah로 환산
    nominal_capacity_ah = (nominal_capacity_kwh * 1000) / avg_voltage
    current_capacity_ah = max(nominal_capacity_ah - capacity_loss_ah, 0)
    
    # SOH
    soh_raw = (current_capacity_ah / nominal_capacity_ah) * 100 if nominal_capacity_ah > 0 else 0
    soh = max(60.0, min(105.0, soh_raw))
    
    return SOHEstimate(
        soh=round(soh, 1),
        method="energy_throughput",
        confidence=0.7,
        assumptions=f"평균전압={avg_voltage}V (화학={battery_chemistry}), 노화계수={aging_factor:.6f}",
        details={
            "throughput_kwh": round(throughput_kwh, 1),
            "throughput_ah_equivalent": round(throughput_ah, 1),
            "estimated_capacity_ah": round(current_capacity_ah, 1),
            "avg_voltage_assumed": avg_voltage,
            "chemistry": battery_chemistry,
        }
    )


def estimate_soh_multimethod(signals: dict, car: dict | None) -> SOHEstimate:
    """
    여러 방법을 조합하여 가장 신뢰도 높은 SOH 추정.
    
    우선순위:
    1. Coulomb Counting (Ah 기반) - 가장 정확
    2. Energy Throughput (kWh 기반) - Ah가 없을 때
    3. Time-based (시간 기반) - 최후 수단
    
    Args:
        signals: 수집된 신호 딕셔너리
        car: 차량 정보 (모델명, 연료 등)
    
    Returns:
        가장 신뢰도 높은 SOHEstimate (또는 여러 추정의 가중 평균)
    """
    spec = get_battery_spec(car)
    estimates = []
    
    # 방법 1: Coulomb Counting (최우선)
    if "cum_charge_ah" in signals and "cum_discharge_ah" in signals:
        try:
            result = estimate_soh_by_throughput(
                signals["cum_charge_ah"],
                signals["cum_discharge_ah"],
                spec["capacity_ah"],
                spec["chemistry"]
            )
            if result.soh is not None:
                estimates.append(result)
        except (ValueError, ZeroDivisionError):
            pass
    
    # 방법 2: Energy Throughput (Ah가 없을 때)
    if "cum_charge_kwh" in signals and "cum_discharge_kwh" in signals:
        try:
            result = estimate_soh_by_energy(
                signals["cum_charge_kwh"],
                signals["cum_discharge_kwh"],
                spec["capacity_kwh"],
                spec["chemistry"]
            )
            if result.soh is not None and not estimates:  # Coulomb이 없을 때만
                estimates.append(result)
        except (ValueError, ZeroDivisionError):
            pass
    
    # 방법 3: Time-based (최후 수단)
    if "operating_time_s" in signals and "cum_charge_kwh" in signals:
        try:
            result = estimate_soh_by_time(
                signals["operating_time_s"],
                signals["cum_charge_kwh"],
                spec["capacity_kwh"]
            )
            if result.soh is not None and not estimates:  # 다른 방법이 없을 때만
                estimates.append(result)
        except (ValueError, ZeroDivisionError):
            pass
    
    # 추정 불가
    if not estimates:
        return SOHEstimate(
            soh=None,
            method="unavailable",
            confidence=0.0,
            assumptions="SOH 추정에 필요한 데이터가 부족합니다.",
            details={
                "available_signals": list(signals.keys()),
                "required_any": [
                    ["cum_charge_ah", "cum_discharge_ah"],
                    ["cum_charge_kwh", "cum_discharge_kwh"],
                    ["operating_time_s", "cum_charge_kwh"]
                ],
                "battery_spec": spec
            }
        )
    
    # 단일 추정만 있으면 그대로 반환
    if len(estimates) == 1:
        return estimates[0]
    
    # 여러 추정이 있으면 가중 평균 (신뢰도 기반)
    total_weight = sum(e.confidence for e in estimates)
    if total_weight == 0:
        return estimates[0]
    
    weighted_soh = sum(e.soh * e.confidence for e in estimates) / total_weight
    
    return SOHEstimate(
        soh=round(weighted_soh, 1),
        method="fusion",
        confidence=round(total_weight / len(estimates), 2),
        assumptions=f"{len(estimates)}개 방법의 가중 평균 (신뢰도 기반)",
        details={
            "individual_estimates": [
                {
                    "soh": e.soh,
                    "method": e.method,
                    "confidence": e.confidence,
                    "assumptions": e.assumptions
                }
                for e in estimates
            ],
            "battery_spec": spec
        }
    )


def format_soh_result(estimate: SOHEstimate) -> dict:
    """SOH 추정 결과를 사용자 친화적 형식으로 변환."""
    if estimate.soh is None:
        return {
            "soh": None,
            "status": "unavailable",
            "message": "SOH를 추정할 수 없습니다.",
            "reason": estimate.assumptions,
            "method": estimate.method,
            "details": estimate.details
        }
    
    # SOH 상태 판정
    if estimate.soh >= 90:
        status = "excellent"
        status_text = "매우 좋음 (신품에 가까움)"
    elif estimate.soh >= 80:
        status = "good"
        status_text = "좋음 (정상 범위)"
    elif estimate.soh >= 70:
        status = "fair"
        status_text = "보통 (교체 권장 검토)"
    else:
        status = "poor"
        status_text = "나쁨 (교체 권장)"
    
    return {
        "soh": estimate.soh,
        "status": status,
        "status_text": status_text,
        "message": f"배터리 건강도(SOH): {estimate.soh}% ({status_text})",
        "method": estimate.method,
        "confidence": estimate.confidence,
        "confidence_text": f"신뢰도 {int(estimate.confidence * 100)}%",
        "assumptions": estimate.assumptions,
        "details": estimate.details
    }
