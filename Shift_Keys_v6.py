bl_info = {
    "name": "Shift Keys",
    "author": "Claude",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "location": "Graph Editor / Dope Sheet > Sidebar > Shift Keys",
    "description": ("Shift the selected keyframes uniformly by a precise amount, or shift them so the "
                    "key at the playhead takes a value you set"),
    "category": "Animation",
}

import math
import re

import bpy
from bpy.props import EnumProperty, FloatProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup


DIRECTION_ITEMS = [
    ('RIGHT', "Right (later)", "Move keys to later frames (+X)", 'TRIA_RIGHT', 0),
    ('LEFT', "Left (earlier)", "Move keys to earlier frames (-X)", 'TRIA_LEFT', 1),
    ('UP', "Up (higher value)", "Increase the key values (+Y)", 'TRIA_UP', 2),
    ('DOWN', "Down (lower value)", "Decrease the key values (-Y)", 'TRIA_DOWN', 3),
]

FRAME_TOLERANCE = 1e-4   # how close a key must be to the playhead frame to count as "on" it


class ShiftKeysSettings(PropertyGroup):
    direction: EnumProperty(
        name="Direction",
        description="Direction in which the selected keys are moved",
        items=DIRECTION_ITEMS,
        default='RIGHT',
    )
    amount: FloatProperty(
        name="Amount",
        description=("How far to shift the keys. Frames for Left/Right; for Up/Down, the units each "
                     "channel is shown in (e.g. degrees for rotation curves, scene length unit for location)"),
        default=1.0,
        min=0.0,
        soft_max=1000.0,
        precision=10,
        step=100,
    )


# One "Value" property per Blender unit category. Each is a real unit-aware property, so the field
# displays exactly like the animated property itself does (e.g. 14740° or 1 m 31.1 cm), and what is
# typed is already in the F-curve's stored units.
VALUE_UNITS = (
    'NONE', 'ROTATION', 'LENGTH', 'AREA', 'VOLUME', 'MASS', 'CAMERA', 'TIME',
    'TIME_ABSOLUTE', 'VELOCITY', 'ACCELERATION', 'POWER', 'TEMPERATURE',
)
VALUE_UNIT_TITLES = {
    'NONE': "Value", 'ROTATION': "Rotation", 'LENGTH': "Length", 'AREA': "Area", 'VOLUME': "Volume",
    'MASS': "Mass", 'CAMERA': "Camera", 'TIME': "Time", 'TIME_ABSOLUTE': "Time",
    'VELOCITY': "Velocity", 'ACCELERATION': "Acceleration", 'POWER': "Power",
    'TEMPERATURE': "Temperature",
}


def _value_prop_name(unit):
    return "value_" + (unit if unit in VALUE_UNITS else 'NONE').lower()


for _unit in VALUE_UNITS:
    ShiftKeysSettings.__annotations__[_value_prop_name(_unit)] = FloatProperty(
        name=VALUE_UNIT_TITLES[_unit],
        description=("The value the key at the playhead should end up with, shown in the same units as "
                     "the animated channel. The whole key selection moves by the same offset"),
        default=0.0,
        unit=_unit,
        precision=10,
        step=100,
    )


def _channelbag_fcurves(action, slot):
    """All F-curves an action slot uses (works with Blender's slotted actions and older versions)."""
    if action is None:
        return []
    try:
        from bpy_extras import anim_utils
        if slot is not None:
            cb = anim_utils.action_get_channelbag_for_slot(action, slot)
            if cb is not None:
                return list(cb.fcurves)
            return []
    except Exception:
        pass
    return list(getattr(action, "fcurves", []))


def _anim_owners(ob):
    yield ob
    data = getattr(ob, "data", None)
    if data is not None:
        yield data
        sk = getattr(data, "shape_keys", None)
        if sk is not None:
            yield sk
    for slot in getattr(ob, "material_slots", []):
        if slot.material is not None:
            yield slot.material
            if slot.material.node_tree is not None:
                yield slot.material.node_tree


def _fcurves_from_selection(context):
    seen, out = set(), []
    for ob in context.selected_objects:
        for owner in _anim_owners(ob):
            ad = getattr(owner, "animation_data", None)
            if ad is None or ad.action is None:
                continue
            for fc in _channelbag_fcurves(ad.action, getattr(ad, "action_slot", None)):
                key = fc.as_pointer()
                if key not in seen:
                    seen.add(key)
                    out.append(fc)
    return out


def _target_fcurves(context):
    """Graph Editor: the visible, editable curves. Elsewhere (Dope Sheet): curves of the selected objects."""
    fcs = getattr(context, "editable_fcurves", None)
    if fcs:
        return list(fcs)
    return _fcurves_from_selection(context)


def _all_animation_owners():
    """Every ID (and embedded node tree / shape-key datablock) in the file that can carry animation."""
    seen = set()
    for attr in dir(bpy.data):
        coll = getattr(bpy.data, attr, None)
        if not isinstance(coll, bpy.types.bpy_prop_collection):
            continue
        for idb in coll:
            if not isinstance(idb, bpy.types.ID):
                continue
            for owner in (idb, getattr(idb, "node_tree", None)):
                if owner is None:
                    continue
                ptr = owner.as_pointer()
                if ptr not in seen:
                    seen.add(ptr)
                    yield owner


def _build_fcurve_owner_map():
    """Maps each F-curve's pointer to the datablock it animates (needed to look up the property's unit)."""
    out = {}
    for owner in _all_animation_owners():
        ad = getattr(owner, "animation_data", None)
        if ad is None or ad.action is None:
            continue
        for fc in _channelbag_fcurves(ad.action, getattr(ad, "action_slot", None)):
            out.setdefault(fc.as_pointer(), owner)
    return out


LENGTH_UNIT_METERS = {
    'KILOMETERS': 1000.0, 'METERS': 1.0, 'CENTIMETERS': 0.01, 'MILLIMETERS': 0.001, 'MICROMETERS': 1e-6,
    'MILES': 1609.344, 'YARDS': 0.9144, 'FEET': 0.3048, 'INCHES': 0.0254, 'THOUSANDTH': 0.0000254,
}
LENGTH_UNIT_LABELS = {
    'KILOMETERS': "km", 'METERS': "m", 'CENTIMETERS': "cm", 'MILLIMETERS': "mm", 'MICROMETERS': "um",
    'MILES': "mi", 'YARDS': "yd", 'FEET': "ft", 'INCHES': "in", 'THOUSANDTH': "thou",
}
MASS_UNIT_KG = {
    'TONNES': 1000.0, 'KILOGRAMS': 1.0, 'GRAMS': 0.001, 'MILLIGRAMS': 1e-6,
    'POUNDS': 0.45359237, 'OUNCES': 0.028349523125,
}
MASS_UNIT_LABELS = {
    'TONNES': "t", 'KILOGRAMS': "kg", 'GRAMS': "g", 'MILLIGRAMS': "mg", 'POUNDS': "lb", 'OUNCES': "oz",
}


def _channel_unit_category(fc, owner):
    """The Blender unit category of the property an F-curve animates ('ROTATION', 'LENGTH', 'NONE', ...)."""
    if owner is None:
        return 'NONE'
    path = fc.data_path
    dot = path.rfind(".")
    name = path[dot + 1:] if dot >= 0 else path
    if not name.isidentifier():          # custom property ["x"], etc. -> no unit
        return 'NONE'
    try:
        parent = owner.path_resolve(path[:dot]) if dot >= 0 else owner
        prop = parent.bl_rna.properties.get(name)
        unit = prop.unit if prop is not None else 'NONE'
        return unit if unit in VALUE_UNITS else 'NONE'
    except Exception:
        return 'NONE'


def _category_unit_info(category, scene):
    """
    Returns (factor, label) for a unit category: factor converts the stored value into the value shown in
    the editor (shown = stored * factor), label names that unit.
    Rotation: radians stored, degrees shown when the scene uses degrees.
    Length / mass: follow the scene's unit system, length unit, mass unit and unit scale.
    Everything else is shown as stored.
    """
    us = scene.unit_settings

    if category == 'ROTATION':
        if us.system_rotation == 'DEGREES':
            return 180.0 / math.pi, "degrees"
        return 1.0, "radians"

    if category == 'LENGTH':
        if us.system == 'NONE':
            return 1.0, "units"
        unit = us.length_unit
        if unit == 'ADAPTIVE':
            unit = 'FEET' if us.system == 'IMPERIAL' else 'METERS'
        size = LENGTH_UNIT_METERS.get(unit, 1.0)
        scale = us.scale_length if us.scale_length else 1.0
        return scale / size, LENGTH_UNIT_LABELS.get(unit, "m")

    if category == 'MASS':
        if us.system == 'NONE':
            return 1.0, "units"
        unit = us.mass_unit
        if unit == 'ADAPTIVE':
            unit = 'POUNDS' if us.system == 'IMPERIAL' else 'KILOGRAMS'
        return 1.0 / MASS_UNIT_KG.get(unit, 1.0), MASS_UNIT_LABELS.get(unit, "kg")

    return 1.0, "as stored"


def _display_unit_info(fc, owner_map, scene):
    category = _channel_unit_category(fc, owner_map.get(fc.as_pointer()))
    return _category_unit_info(category, scene)


def _display_unit_factor(fc, owner_map, scene):
    return _display_unit_info(fc, owner_map, scene)[0]


def _selection_units(context):
    """Unit categories of the channels that have selected keys (cheap: looks at selected objects only)."""
    owner_map = {}
    for ob in context.selected_objects:
        for owner in _anim_owners(ob):
            ad = getattr(owner, "animation_data", None)
            if ad is not None and ad.action is not None:
                for fc in _channelbag_fcurves(ad.action, getattr(ad, "action_slot", None)):
                    owner_map.setdefault(fc.as_pointer(), owner)
    units = []
    for fc in _target_fcurves(context):
        if any(kp.select_control_point for kp in fc.keyframe_points):
            unit = _channel_unit_category(fc, owner_map.get(fc.as_pointer()))
            if unit not in units:
                units.append(unit)
    return units


_NUMBER_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _parse_clipboard_number(text):
    """
    Pull a number out of text such as '32,600', '14 960', '1.234,5' or '90 degrees'.
    Thousands separators are dropped (Blender's own fields would add the parts instead: 32,600 -> 632).
    Returns None when no number is found.
    """
    t = (text or "").strip().replace("\u00a0", " ").replace("\u202f", " ")
    m = re.search(r"[-+]?\d[\d,. ]*", t)
    if not m:
        return None
    chunk = m.group(0).strip().rstrip(",. ")
    sign = -1.0 if t[:m.start()].rstrip().endswith("-") and not chunk.startswith(("-", "+")) else 1.0
    chunk = chunk.replace(" ", "")
    if "," in chunk and "." in chunk:
        # the right-most separator is the decimal point, the other kind is a thousands separator
        if chunk.rfind(",") > chunk.rfind("."):
            chunk = chunk.replace(".", "").replace(",", ".")
        else:
            chunk = chunk.replace(",", "")
    elif "," in chunk:
        if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", chunk):
            chunk = chunk.replace(",", "")          # 32,600 -> thousands separators
        else:
            chunk = chunk.replace(",", ".")         # 3,5 -> decimal comma
    elif chunk.count(".") > 1:
        chunk = chunk.replace(".", "")              # 1.234.567 -> thousands separators
    m2 = _NUMBER_RE.match(chunk)
    if not m2:
        return None
    try:
        return sign * float(m2.group(0))
    except ValueError:
        return None


def _move_key(kp, dx, dy):
    """Move a key and both handles by the same offset so the curve shape is preserved."""
    kp.co.x += dx
    kp.co.y += dy
    kp.handle_left.x += dx
    kp.handle_left.y += dy
    kp.handle_right.x += dx
    kp.handle_right.y += dy


def _redraw_animation_areas(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'GRAPH_EDITOR', 'DOPESHEET_EDITOR', 'TIMELINE'}:
                area.tag_redraw()
    context.view_layer.update()


class ANIM_OT_shift_selected_keys(Operator):
    bl_idname = "anim.shift_selected_keys"
    bl_label = "Shift Keys"
    bl_description = "Move every selected keyframe (with its handles) by the chosen amount in the chosen direction"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type in {'GRAPH_EDITOR', 'DOPESHEET_EDITOR'}

    def execute(self, context):
        s = context.scene.shift_keys_settings
        amount = float(s.amount)
        dx = dy = 0.0
        if s.direction == 'RIGHT':
            dx = amount
        elif s.direction == 'LEFT':
            dx = -amount
        elif s.direction == 'UP':
            dy = amount
        else:
            dy = -amount

        vertical = s.direction in {'UP', 'DOWN'}
        owner_map = _build_fcurve_owner_map() if vertical else {}

        n_keys = n_curves = 0
        for fc in _target_fcurves(context):
            if fc.lock:
                continue
            # Up/Down: the amount is in the channel's display units, so convert to stored units per curve.
            curve_dy = dy / _display_unit_factor(fc, owner_map, context.scene) if vertical else 0.0
            moved = False
            for kp in fc.keyframe_points:
                if not kp.select_control_point:
                    continue
                _move_key(kp, dx, curve_dy)
                n_keys += 1
                moved = True
            if moved:
                n_curves += 1
                fc.update()      # re-sorts the keys if they passed over neighbours

        if n_keys == 0:
            self.report({'WARNING'}, "No selected keyframes found (select keys in the editor first)")
            return {'CANCELLED'}

        _redraw_animation_areas(context)

        label = {'RIGHT': "right", 'LEFT': "left", 'UP': "up", 'DOWN': "down"}[s.direction]
        self.report({'INFO'}, "Shifted %d keys on %d curves %s by %.10f" % (n_keys, n_curves, label, amount))
        return {'FINISHED'}


class ANIM_OT_shift_keys_to_value(Operator):
    bl_idname = "anim.shift_keys_to_value"
    bl_label = "Shift Selection to Value"
    bl_description = ("Shift the selected keyframes up or down so the selected key sitting on the current "
                      "frame (the playhead) gets the chosen value. Each curve is shifted by its own offset, "
                      "so all the selected keys on that curve keep their spacing relative to each other")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type in {'GRAPH_EDITOR', 'DOPESHEET_EDITOR'}

    def execute(self, context):
        s = context.scene.shift_keys_settings
        frame = context.scene.frame_current
        owner_map = _build_fcurve_owner_map()

        n_keys = n_curves = n_no_ref = 0
        for fc in _target_fcurves(context):
            if fc.lock:
                continue
            selected = [kp for kp in fc.keyframe_points if kp.select_control_point]
            if not selected:
                continue

            # The reference key: the selected key on the playhead frame.
            ref = None
            for kp in selected:
                if abs(kp.co.x - frame) <= FRAME_TOLERANCE:
                    ref = kp
                    break
            if ref is None:
                n_no_ref += 1
                continue

            # The Value field matching this channel's unit already holds the stored-unit value.
            unit = _channel_unit_category(fc, owner_map.get(fc.as_pointer()))
            target = float(getattr(s, _value_prop_name(unit)))
            dy = target - ref.co.y
            for kp in selected:
                _move_key(kp, 0.0, dy)
                n_keys += 1
            n_curves += 1
            fc.update()

        if n_curves == 0:
            if n_no_ref:
                self.report({'WARNING'}, "No selected key on the current frame (frame %d). "
                                         "Move the playhead onto a selected key" % frame)
            else:
                self.report({'WARNING'}, "No selected keyframes found (select keys in the editor first)")
            return {'CANCELLED'}

        _redraw_animation_areas(context)

        msg = "Moved %d keys on %d curves so the key at frame %d matches the Value" % (n_keys, n_curves, frame)
        if n_no_ref:
            msg += " (%d curves skipped: no selected key on this frame)" % n_no_ref
        self.report({'INFO'}, msg)
        return {'FINISHED'}


class ANIM_OT_shift_keys_paste_value(Operator):
    bl_idname = "anim.shift_keys_paste_value"
    bl_label = "Paste Value"
    bl_description = ("Fill the Value field from the clipboard, in the units the channel is shown in. "
                      "Thousands separators are handled (32,600 becomes 32600), unlike typing a comma into "
                      "a Blender field, which adds the parts together")

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type in {'GRAPH_EDITOR', 'DOPESHEET_EDITOR'}

    def execute(self, context):
        number = _parse_clipboard_number(context.window_manager.clipboard)
        if number is None:
            self.report({'WARNING'}, "No number found on the clipboard")
            return {'CANCELLED'}
        s = context.scene.shift_keys_settings
        units = _selection_units(context) or ['NONE']
        for unit in units:
            factor = _category_unit_info(unit, context.scene)[0] or 1.0
            setattr(s, _value_prop_name(unit), number / factor)
        _redraw_animation_areas(context)
        self.report({'INFO'}, "Value set to %s" % ("{:,.10g}".format(number)))
        return {'FINISHED'}


def _draw_shift_panel(layout, context):
    s = context.scene.shift_keys_settings
    col = layout.column(align=True)
    col.use_property_split = False
    col.label(text="Direction")
    col.prop(s, "direction", expand=True)
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(s, "amount")
    layout.operator(ANIM_OT_shift_selected_keys.bl_idname, icon='NLA_PUSHDOWN')

    layout.separator()
    box = layout.box()
    box.use_property_split = True
    box.use_property_decorate = False
    box.label(text="Set Playhead Key Value")
    units = _selection_units(context)
    if not units:
        box.prop(s, _value_prop_name('NONE'), text="Value")
        box.label(text="Select keys to detect their units", icon='INFO')
    elif len(units) == 1:
        box.prop(s, _value_prop_name(units[0]), text="Value")
    else:
        for unit in units:      # mixed channel types: one field per unit, each applied to its own curves
            box.prop(s, _value_prop_name(unit), text=VALUE_UNIT_TITLES[unit])
    box.operator(ANIM_OT_shift_keys_paste_value.bl_idname, icon='PASTEDOWN')
    box.operator(ANIM_OT_shift_keys_to_value.bl_idname, icon='KEY_HLT')


class GRAPH_PT_shift_keys(Panel):
    bl_label = "Shift Keys"
    bl_idname = "GRAPH_PT_shift_keys"
    bl_space_type = 'GRAPH_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Shift Keys"

    def draw(self, context):
        _draw_shift_panel(self.layout, context)


class DOPESHEET_PT_shift_keys(Panel):
    bl_label = "Shift Keys"
    bl_idname = "DOPESHEET_PT_shift_keys"
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Shift Keys"

    def draw(self, context):
        _draw_shift_panel(self.layout, context)


classes = (
    ShiftKeysSettings,
    ANIM_OT_shift_selected_keys,
    ANIM_OT_shift_keys_to_value,
    ANIM_OT_shift_keys_paste_value,
    GRAPH_PT_shift_keys,
    DOPESHEET_PT_shift_keys,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.shift_keys_settings = PointerProperty(type=ShiftKeysSettings)


def unregister():
    del bpy.types.Scene.shift_keys_settings
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
