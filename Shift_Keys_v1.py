bl_info = {
    "name": "Shift Keys",
    "author": "Claude",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "Graph Editor / Dope Sheet > Sidebar > Shift Keys",
    "description": "Shift the selected keyframes uniformly in a chosen direction by a precise amount",
    "category": "Animation",
}

import bpy
from bpy.props import EnumProperty, FloatProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup


DIRECTION_ITEMS = [
    ('RIGHT', "Right (later)", "Move keys to later frames (+X)", 'TRIA_RIGHT', 0),
    ('LEFT', "Left (earlier)", "Move keys to earlier frames (-X)", 'TRIA_LEFT', 1),
    ('UP', "Up (higher value)", "Increase the key values (+Y)", 'TRIA_UP', 2),
    ('DOWN', "Down (lower value)", "Decrease the key values (-Y)", 'TRIA_DOWN', 3),
]


class ShiftKeysSettings(PropertyGroup):
    direction: EnumProperty(
        name="Direction",
        description="Direction in which the selected keys are moved",
        items=DIRECTION_ITEMS,
        default='RIGHT',
    )
    amount: FloatProperty(
        name="Amount",
        description=("How far to shift the keys. Frames for Left/Right, curve value units for "
                     "Up/Down (rotation curves are in radians)"),
        default=1.0,
        min=0.0,
        soft_max=1000.0,
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

        n_keys = n_curves = 0
        for fc in _target_fcurves(context):
            if fc.lock:
                continue
            moved = False
            for kp in fc.keyframe_points:
                if not kp.select_control_point:
                    continue
                # Move the key and both handles by the same offset so the curve shape is preserved.
                kp.co.x += dx
                kp.co.y += dy
                kp.handle_left.x += dx
                kp.handle_left.y += dy
                kp.handle_right.x += dx
                kp.handle_right.y += dy
                n_keys += 1
                moved = True
            if moved:
                n_curves += 1
                fc.update()      # re-sorts the keys if they passed over neighbours

        if n_keys == 0:
            self.report({'WARNING'}, "No selected keyframes found (select keys in the editor first)")
            return {'CANCELLED'}

        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'GRAPH_EDITOR', 'DOPESHEET_EDITOR', 'TIMELINE'}:
                    area.tag_redraw()
        context.view_layer.update()

        label = {'RIGHT': "right", 'LEFT': "left", 'UP': "up", 'DOWN': "down"}[s.direction]
        self.report({'INFO'}, "Shifted %d keys on %d curves %s by %.10f" % (n_keys, n_curves, label, amount))
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
