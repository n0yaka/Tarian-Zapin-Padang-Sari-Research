"""
attach_trackers_to_avatar.py
Makes your avatar's skeleton follow the three tracker markers created by
import_trackers_to_blender.py, and optionally bakes the result into a normal animation.

WHAT GETS DRIVEN
- Hips bone   -> follows the hip tracker's position and turning; how much of its tilt is
                 copied is set by HIP_TILT
- Legs        -> IK: each foot is pulled to its ankle tracker, the knee bend is worked out
                 automatically, and the knees point the way the hips face
- Feet        -> "flat" (default): stay level with the floor and turn with the ankle tracker
                 "follow": copy the ankle tracker's full rotation (tilts with the shin)
- Takes WITH the Quest headset and controllers (mocap_recorder.py):
    Spine -> IK leans the upper spine (kept straight) so the top of the chest follows the headset
    Head  -> turns with the headset
    Arms  -> upper arm and forearm point the way the dancer's did, worked out from each controller's
             position and rotation. The hands land where the avatar's own arms put them, not exactly
             on the controllers (the avatar's arms are a different length).
    Hands -> turn with the controllers (how the controller sits in the hand is estimated from the take);
             the forearm takes half of that twist
- Takes WITHOUT them: everything above the hips moves rigidly with the hips.
- At the "calibrate" frame the dancer is assumed to stand upright and look straight ahead, like the
  avatar's rest pose. Every offset between a tracker and its bone is fixed at that frame.

BEFORE RUNNING
1. Your avatar needs a skeleton (armature) with a hips bone and, for each leg, a thigh,
   shin and foot bone. A plain skeleton works (one you built yourself, or one from Mixamo).
   Rigify control rigs need a different setup; ask if that's what you have.
2. Stand the avatar in its normal rest pose, on the floor (height 0), at the world centre,
   facing Blender's front view (-Y). Apply its scale (Ctrl+A > Scale) if it isn't 1.
   The skeleton shouldn't have any other animation on it yet.
3. Run import_trackers_to_blender.py first, WITH LEFT_/RIGHT_ANKLE_SERIAL filled in
   (this script needs markers named Tracker_Ankle_L and Tracker_Ankle_R).
4. Fill in ARMATURE_NAME and BONES below. If a name is wrong, the script stops and prints
   every bone name in your skeleton so you can copy the right ones.

HOW TO USE
Scripting tab > Text > Open > this file > change SETTINGS > Run Script.
Then press Space to play. If you're happy, set BAKE = True and run again (or bake by hand:
Pose mode > select all bones > Pose > Animation > Bake Action, tick Visual Keying and
Clear Constraints).
"""

import bpy
import math
from mathutils import Vector, Matrix, Quaternion

# ============================== SETTINGS ==============================
ARMATURE_NAME = "Armature"          # the skeleton object's name in the Outliner

BONES = {                           # your bone names (these are BASEmodel.blend's)
    "hips":    "wiest",
    "thigh_L": "upperleg.L",
    "shin_L":  "lowerleg.L",
    "foot_L":  "foot.L",
    "thigh_R": "upperleg.R",
    "shin_R":  "lowerleg.R",
    "foot_R":  "foot.R",
    # Upper body: only used when the take has the Quest headset and controllers. Delete these to skip.
    "chest":   "chest",             # top spine bone; the spine leans so its tip follows the headset
    "head":    "head",
    "upper_arm_L": "upperarm.L", "forearm_L": "lowerarm.L", "hand_L": "hand.L",
    "upper_arm_R": "upperarm.R", "forearm_R": "lowerarm.R", "hand_R": "hand.R",
}
SPINE_BONES = 2                     # spine bones from "chest" down to just above "hips" (BASEmodel: chest, hip)
# AVATAR.blend: ARMATURE_NAME "metarig"; hips spine, thigh/shin/foot.L/R, chest spine.003 (SPINE_BONES 3),
# head spine.006, upper_arm/forearm/hand.L/R

FEET = "flat"                       # "flat" or "follow"
HIP_TILT = 1.0                      # 1 = hips copy the hip tracker's full tilt; 0 = hips stay
                                    # upright and only turn. Lower it if the upper body rocks too much.
HIP_DROP = 0.05                     # metres; lowers the hips a little so the knees have slack
                                    # (raise it by 0.02 at a time if legs are reported over-stretched)
BAKE = False                        # True = bake to keyframes and remove the constraints
# =====================================================================

TRACKERS = {"hip": "Tracker_Hip", "L": "Tracker_Ankle_L", "R": "Tracker_Ankle_R"}
QUEST = {"head": "Quest_Head", "L": "Quest_Hand_L", "R": "Quest_Hand_R"}
UPPER = ["chest", "head", "upper_arm_L", "forearm_L", "hand_L", "upper_arm_R", "forearm_R", "hand_R"]
TAG = "Mocap"                       # name prefix for everything this script adds


def fail(msg):
    raise RuntimeError("\n\n" + msg + "\n")


def keep_transform_parent(obj, parent, world_matrix):
    """Parent obj to parent without moving it (like Ctrl+P > Keep Transform)."""
    obj.parent = parent
    obj.matrix_parent_inverse = parent.matrix_world.inverted()
    obj.matrix_basis = world_matrix


def new_empty(name, coll, size, display="SPHERE"):
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = display
    obj.empty_display_size = size
    coll.objects.link(obj)
    return obj


def signed_angle(u, v, normal):
    a = u.angle(v)
    if u.cross(v).angle(normal) < 1:
        a = -a
    return a


def pole_angle(thigh_pb, shin_pb, pole_arm_space):
    """IK pole angle that keeps the leg exactly in its rest pose (no twist when IK switches on)."""
    base_x = thigh_pb.matrix.to_3x3().col[0]
    chain = thigh_pb.tail - thigh_pb.head
    pole_normal = (shin_pb.tail - thigh_pb.head).cross(pole_arm_space - thigh_pb.head)
    projected = pole_normal.cross(chain)
    return signed_angle(base_x, projected, chain)


def standing_frames(scene, head, step):
    """Frames where the headset is above 1.2 m, i.e. the dancer is up (not sitting or getting ready)."""
    for f in range(scene.frame_start, scene.frame_end + 1, step):
        scene.frame_set(f)
        if head.matrix_world.translation.z > 1.2:
            yield f


def attach_upper_body(arm, pb, quest, coll, scene):
    """Spine and head follow the headset, arms point the way the dancer's did, hands turn with the controllers.
    Run at the calibrate frame, after the hips and legs are attached. Returns check results."""
    W = arm.matrix_world
    up = bpy.context.view_layer.update
    head_pitch = lambda: math.degrees(math.asin(
        (quest["head"].matrix_world.to_3x3().normalized() @ Vector((0, 1, 0))).z))   # headset looks along its +Y here
    res = {"pitch_cal": head_pitch()}
    cal_frame = scene.frame_current

    # spine + head: offsets from the headset fixed now, with the dancer upright
    t_neck = new_empty("Target_Neck", coll, 0.05)
    keep_transform_parent(t_neck, quest["head"], Matrix.Translation(W @ pb["chest"].tail))
    t_head = new_empty("Target_Head", coll, 0.06, "ARROWS")
    keep_transform_parent(t_head, quest["head"], W @ pb["head"].matrix)
    ik = pb["chest"].constraints.new("IK")
    ik.name, ik.target, ik.chain_count, ik.use_tail = f"{TAG} spine", t_neck, SPINE_BONES, True
    b = pb["chest"]
    for _ in range(SPINE_BONES - 1):    # only the lowest spine bone turns; a free chain folds the belly forward
        b.lock_ik_x = b.lock_ik_y = b.lock_ik_z = True
        b = b.parent
    c = pb["head"].constraints.new("COPY_ROTATION")
    c.name, c.target = f"{TAG} head", t_head
    up()

    # Arms are aimed, not stretched to the controllers. The avatar's arm proportions differ from the
    # dancer's (BASEmodel's forearm is half its upper arm), so reaching for the controller's position bends
    # the elbow and wrist the wrong way. Instead the upper arm and forearm point the way the dancer's did.
    # Forearm direction in the controller's axes: with the arm stretched out (top 15% of reach) the forearm
    # lies along shoulder-to-controller, so average that direction as the controller saw it.
    seen = {"L": [], "R": []}           # (shoulder-to-controller distance, that direction in the controller's axes)
    for _ in standing_frames(scene, quest["head"], 10):
        for s in ("L", "R"):
            v = quest[s].matrix_world.translation - W @ pb[f"upper_arm_{s}"].head
            seen[s].append((v.length, quest[s].matrix_world.to_3x3().normalized().inverted() @ v.normalized()))
    scene.frame_set(cal_frame)
    for s in ("L", "R"):
        far = sorted(seen[s], key=lambda x: x[0])[int(0.85 * len(seen[s])):]
        fwd = sum((u for _, u in far), Vector()).normalized()
        res[f"forearm_spread_{s}"] = sorted(math.degrees(u.angle(fwd)) for _, u in far)[len(far) // 2]
        # shortcut: 0.30 m is a typical controller-to-elbow length, not measured; it sets where the upper arm aims
        el = new_empty(f"Target_Elbow_{s}", coll, 0.04)
        el.parent, el.location = quest[s], -0.30 * fwd
        aim = new_empty(f"Target_ForearmAim_{s}", coll, 0.02)
        aim.parent, aim.location = quest[s], 10.0 * fwd     # far out along the forearm: aiming at it = its direction
        for key, tgt in ((f"upper_arm_{s}", el), (f"forearm_{s}", aim)):
            c = pb[key].constraints.new("DAMPED_TRACK")
            c.name, c.target = f"{TAG} aim", tgt
    up()

    # Hand turn = controller turn + how the controller sits in the hand, averaged over the standing frames
    # assuming the wrist is straight on average. The forearm takes half of the controller's turn too, as a
    # real forearm twists along its length, so the wrist doesn't wring.
    rel = {k: [] for k in ("hand_L", "hand_R", "forearm_L", "forearm_R")}
    for _ in standing_frames(scene, quest["head"], 10):
        for k in rel:
            rel[k].append(quest[k[-1]].matrix_world.to_quaternion().inverted() @ (W @ pb[k].matrix).to_quaternion())
    for k, qs in rel.items():
        mean = Quaternion((0.0, 0.0, 0.0, 0.0))
        for q in qs:
            mean = mean + (q if q.dot(qs[0]) >= 0 else -q)
        mean.normalize()
        h = new_empty(f"Target_{k.split('_')[0].title()}Rot_{k[-1]}", coll, 0.04, "ARROWS")
        h.parent = quest[k[-1]]
        h.rotation_mode = "QUATERNION"
        h.rotation_quaternion = mean
        c = pb[k].constraints.new("COPY_ROTATION")
        c.name, c.target = f"{TAG} turn", h
        c.influence = 0.5 if k.startswith("forearm") else 1.0
    up()

    # checks
    neck, wrist = [], []
    rest = {s: pb[f"forearm_{s}"].bone.matrix_local.to_quaternion().inverted()
            @ pb[f"hand_{s}"].bone.matrix_local.to_quaternion() for s in ("L", "R")}
    for _ in standing_frames(scene, quest["head"], 5):
        neck.append((W @ pb["chest"].tail - t_neck.matrix_world.translation).length)
        for s in ("L", "R"):
            now = pb[f"forearm_{s}"].matrix.to_quaternion().inverted() @ pb[f"hand_{s}"].matrix.to_quaternion()
            a = rest[s].rotation_difference(now).angle
            wrist.append(math.degrees(min(a, 2 * math.pi - a)))
    neck.sort()
    wrist.sort()
    res["wrist_median"], res["wrist_p90"] = wrist[len(wrist) // 2], wrist[int(0.9 * (len(wrist) - 1))]
    res["neck_err_median"], res["neck_err_p95"] = neck[len(neck) // 2], neck[int(0.95 * (len(neck) - 1))]
    pitch = sorted(head_pitch() for _ in standing_frames(scene, quest["head"], 10))
    res["pitch_median"] = pitch[len(pitch) // 2]
    return res


def main():
    scene = bpy.context.scene
    arm = bpy.data.objects.get(ARMATURE_NAME)
    if arm is None or arm.type != "ARMATURE":
        names = [o.name for o in bpy.data.objects if o.type == "ARMATURE"]
        fail(f"No skeleton called '{ARMATURE_NAME}'. Skeletons in this file: {names}")
    missing = [v for v in BONES.values() if v not in arm.pose.bones]
    if missing:
        fail(f"These bones aren't in '{arm.name}': {missing}\n"
             f"Bones in your skeleton:\n  " + "\n  ".join(b.name for b in arm.pose.bones))
    trk = {k: bpy.data.objects.get(v) for k, v in TRACKERS.items()}
    if any(o is None for o in trk.values()):
        fail("Tracker markers not found. Run import_trackers_to_blender.py first, with "
             "LEFT_ANKLE_SERIAL and RIGHT_ANKLE_SERIAL filled in.")
    coll = trk["hip"].users_collection[0]
    pb = {k: arm.pose.bones[v] for k, v in BONES.items()}

    # --- clean up a previous run ---
    for b in pb.values():
        for c in [c for c in b.constraints if c.name.startswith(TAG)]:
            b.constraints.remove(c)
    for o in list(bpy.data.objects):
        if o.name.startswith(("Target_", "Pole_")) and o.type == "EMPTY":
            bpy.data.objects.remove(o, do_unlink=True)

    # --- go to the standing-still frame and put the legs in rest pose ---
    cal = [m.frame for m in scene.timeline_markers if m.name == "calibrate"]
    scene.frame_set(cal[0] if cal else scene.frame_start)
    for b in pb.values():
        b.location = (0, 0, 0)
        b.rotation_quaternion = (1, 0, 0, 0)
        b.rotation_euler = (0, 0, 0)
        b.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    W = arm.matrix_world
    world = lambda key: W @ pb[key].matrix

    # --- targets that follow the trackers, starting exactly on the bones ---
    t_hips = new_empty("Target_Hips", coll, 0.08, "CUBE")
    keep_transform_parent(t_hips, trk["hip"], Matrix.Translation((0, 0, -HIP_DROP)) @ world("hips"))
    t_foot, poles = {}, {}
    for s in ("L", "R"):
        t = new_empty(f"Target_Foot_{s}", coll, 0.05)
        keep_transform_parent(t, trk[s], world(f"foot_{s}"))
        t_foot[s] = t
        # pole: half a metre in front of the knee, turning with the hips
        knee = W @ pb[f"shin_{s}"].head
        toe_dir = (W @ pb[f"foot_{s}"].tail) - (W @ pb[f"foot_{s}"].head)
        fwd = Vector((toe_dir.x, toe_dir.y, 0.0))
        fwd = fwd.normalized() if fwd.length > 1e-6 else Vector((0, -1, 0))
        poles[s] = (knee, fwd)
    bpy.context.view_layer.update()

    # "flat" feet: a helper that keeps the foot's rest tilt but takes the tracker's heading
    flat = {}
    for s in ("L", "R"):
        h = new_empty(f"Target_FootFlat_{s}", coll, 0.04, "ARROWS")
        h.rotation_mode = "XYZ"
        h.matrix_world = world(f"foot_{s}")
        cr = h.constraints.new("COPY_ROTATION")
        cr.name, cr.target = f"{TAG} heading", t_foot[s]
        cr.use_x, cr.use_y, cr.use_z = False, False, True
        flat[s] = h
    bpy.context.view_layer.update()

    # upright version of the hips target: same position and heading, no tilt
    h_up = new_empty("Target_HipsUpright", coll, 0.06, "ARROWS")
    h_up.rotation_mode = "XYZ"
    h_up.matrix_world = t_hips.matrix_world.copy()
    cl = h_up.constraints.new("COPY_LOCATION")
    cl.name, cl.target = f"{TAG} position", t_hips
    cr = h_up.constraints.new("COPY_ROTATION")
    cr.name, cr.target = f"{TAG} heading", t_hips
    cr.use_x, cr.use_y, cr.use_z = False, False, True
    bpy.context.view_layer.update()
    for s in ("L", "R"):
        knee, fwd = poles[s]
        p = new_empty(f"Pole_{s}", coll, 0.04, "PLAIN_AXES")
        keep_transform_parent(p, h_up, Matrix.Translation(knee + 0.5 * fwd))
        poles[s] = p
    bpy.context.view_layer.update()

    # --- constraints ---
    c = pb["hips"].constraints.new("COPY_TRANSFORMS")
    c.name, c.target = f"{TAG} hips", h_up
    c = pb["hips"].constraints.new("COPY_ROTATION")
    c.name, c.target, c.influence = f"{TAG} hip tilt", t_hips, max(0.0, min(1.0, HIP_TILT))
    for s in ("L", "R"):
        pole_arm = W.inverted() @ poles[s].matrix_world.translation
        ang = pole_angle(pb[f"thigh_{s}"], pb[f"shin_{s}"], pole_arm)
        ik = pb[f"shin_{s}"].constraints.new("IK")
        ik.name, ik.target, ik.pole_target = f"{TAG} IK", t_foot[s], poles[s]
        ik.pole_angle, ik.chain_count, ik.use_tail = ang, 2, True
        cr = pb[f"foot_{s}"].constraints.new("COPY_ROTATION")
        cr.name = f"{TAG} foot"
        cr.target = flat[s] if FEET == "flat" else t_foot[s]
    bpy.context.view_layer.update()

    quest = {k: bpy.data.objects.get(v) for k, v in QUEST.items()}
    upper = all(quest.values()) and all(k in BONES for k in UPPER)
    if upper:
        ub = attach_upper_body(arm, pb, quest, coll, scene)
        scene.frame_set(cal[0] if cal else scene.frame_start)

    # --- check: can the avatar's legs reach the trackers? ---
    leg_len = {s: pb[f"thigh_{s}"].length + pb[f"shin_{s}"].length for s in ("L", "R")}
    need, tilt = [], []
    r_cal = trk["hip"].matrix_world.to_3x3().normalized()
    for f in range(scene.frame_start, scene.frame_end + 1, 3):
        scene.frame_set(f)
        up = (trk["hip"].matrix_world.to_3x3().normalized() @ r_cal.inverted()) @ Vector((0, 0, 1))
        tilt.append(math.degrees(math.acos(max(-1.0, min(1.0, up.z)))))
        for s in ("L", "R"):
            hip_joint = W @ pb[f"thigh_{s}"].head
            need.append((t_foot[s].matrix_world.translation - hip_joint).length / leg_len[s])
    need.sort()
    p99 = need[int(0.99 * (len(need) - 1))]
    over = sum(1 for n in need if n > 1.0) / len(need) * 100
    scene.frame_set(cal[0] if cal else scene.frame_start)

    tilt.sort()
    tilt90 = tilt[int(0.9 * (len(tilt) - 1))]
    print("\n=== Avatar attached to trackers ===")
    print(f"Skeleton: {arm.name} | feet: {FEET} | HIP_TILT {HIP_TILT} | HIP_DROP {HIP_DROP} m")
    print(f"Hip tracker tilt: 90% of the time under {tilt90:.0f} deg")
    if tilt90 > 15 and HIP_TILT > 0.5:
        print("  -> That's a lot for a pelvis; the tracker may be rocking on its strap. If the upper "
              "body sways unnaturally, try HIP_TILT = 0.5, or strap the tracker tighter / on the lower back.")
    print(f"Leg reach: the avatar's legs are over-stretched in {over:.1f}% of frames")
    if over > 5.0:
        print(f"  -> Raise HIP_DROP (currently {HIP_DROP:.2f} m) by about 0.02 and run again. "
              f"Over-stretched legs lock straight and the feet slide off their trackers.")
    else:
        print("  -> Fine.")
    if upper:
        print("Upper body (standing frames only):")
        print(f"  Headset pitch at the calibrate frame {ub['pitch_cal']:+.0f} deg, usually {ub['pitch_median']:+.0f} deg")
        if abs(ub["pitch_cal"] - ub["pitch_median"]) > 15:
            print("  -> The dancer wasn't looking ahead at the calibrate frame, so the avatar's head will be "
                  "tilted by the difference. Pick another CALIBRATE_WINDOW_S in the import script.")
        print(f"  Top of the spine vs where the headset puts it: median {ub['neck_err_median'] * 100:.1f} cm, "
              f"95% under {ub['neck_err_p95'] * 100:.1f} cm")
        print(f"  Forearm direction from the controller: varies by a median {ub['forearm_spread_L']:.0f} deg (left), "
              f"{ub['forearm_spread_R']:.0f} deg (right) with the arm stretched out")
        print(f"  Wrists: hand turned from its rest angle on the forearm by a median {ub['wrist_median']:.0f} deg, "
              f"90% under {ub['wrist_p90']:.0f} deg")

    if BAKE:
        bpy.ops.object.select_all(action="DESELECT")
        arm.select_set(True)
        bpy.context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="POSE")
        bpy.ops.pose.select_all(action="DESELECT")
        bake = list(pb.values())
        if upper:                        # spine bones between hips and chest are moved by the spine IK too
            b = pb["chest"]
            for _ in range(SPINE_BONES - 1):
                b = b.parent
                bake.append(b)
        for b in bake:
            if hasattr(b, "select"):
                b.select = True          # Blender 5.x
            else:
                b.bone.select = True     # Blender 4.x
        bpy.ops.nla.bake(frame_start=scene.frame_start, frame_end=scene.frame_end,
                         only_selected=True, visual_keying=True, clear_constraints=True,
                         bake_types={"POSE"})
        bpy.ops.object.mode_set(mode="OBJECT")
        print("Baked to keyframes; constraints removed. You can now export (File > Export > FBX).")


main()
