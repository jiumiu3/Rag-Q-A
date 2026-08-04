from decimal import Decimal


class UnitConversionError(ValueError):
    pass


class UnitService:
    """转换到同一物理量的基准单位；全程使用 Decimal。"""

    _UNITS: dict[str, tuple[str, Decimal]] = {
        "mm": ("length", Decimal("0.001")),
        "m": ("length", Decimal("1")),
        "km": ("length", Decimal("1000")),
        "s": ("time", Decimal("1")),
        "min": ("time", Decimal("60")),
        "h": ("time", Decimal("3600")),
        "V": ("voltage", Decimal("1")),
        "kV": ("voltage", Decimal("1000")),
        "A": ("current", Decimal("1")),
        "mm²": ("area", Decimal("1")),
        "mm2": ("area", Decimal("1")),
        "MPa": ("pressure", Decimal("1")),
        "bar": ("pressure", Decimal("0.1")),
        "kPa": ("pressure", Decimal("0.001")),
        "℃": ("temperature", Decimal("1")),
        "°C": ("temperature", Decimal("1")),
        "lx": ("illuminance", Decimal("1")),
        "%": ("ratio", Decimal("1")),
    }

    def convert(self, value: Decimal, source: str, target: str) -> Decimal:
        source_data = self._UNITS.get(source)
        target_data = self._UNITS.get(target)
        if not source_data or not target_data:
            raise UnitConversionError(f"不支持单位换算：{source} -> {target}")
        if source_data[0] != target_data[0]:
            raise UnitConversionError(f"单位物理量不兼容：{source} -> {target}")
        # 摄氏度仅做同单位别名转换，不涉及有偏移量温标。
        return value * source_data[1] / target_data[1]
