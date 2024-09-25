import argparse
import torch
import rerun as rr
from pathlib import Path

from DataLoader import GenericSequence, SourceDataFrame
from Evaluation.EvalSeq import EvaluateSequences
from Odometry.MACVO import MACVO
from Utility.Config import load_config, asNamespace
from Utility.PrettyPrint import print_as_table, ColoredTqdm
from Utility.Space import Sandbox
from Utility.Visualizer import PLTVisualizer
from Utility.Visualizer import RerunVisualizer as rrv


def VisualizeRerunCallback(frame: SourceDataFrame, system: MACVO, pb: ColoredTqdm):
    PLTVisualizer.visualize_stereo("stereo", frame.imageL, frame.imageR)
    rr.set_time_sequence("frame_idx", frame.meta.idx)   #pyright: ignore[reportPrivateImportUsage]
    
    if rrv.status != rrv.State.INACTIVE:
        if system.gmap.frames[-1].FLAG_NEED_INTERP & int(system.gmap.frames.flag[-1].item()) != 0:
            # Non-key frame does not need visualization
            return 
        
        if frame.meta.idx > 0:
            rrv.visualizeTrajectory(system.gmap)
    
        rrv.visualizeFrameAt(system.gmap, -1)
        rrv.visualizeRecentPoints(system.gmap)
        rrv.visualizeImageOnCamera(frame.imageL[0].permute(1, 2, 0))
    
    if torch.cuda.is_available():
        allocated_memory = torch.cuda.memory_reserved(0) / 1e9  # Convert to GB
        allocated_memory = f"{round(allocated_memory, 3)} GB"
    else:
        allocated_memory = "N/A"
    
    pb.set_description(desc=f"{system.gmap}, VRAM={allocated_memory}")


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--odom", type=str, default = "Config/Experiment/MACVO/MACVO.yaml")
    args.add_argument("--data", type=str, default = "Config/Sequence/TartanAir_abandonfac_001.yaml")
    args.add_argument(
        "--seq_to",
        type=int,
        default=-1,
        help="Crop sequence to frame# when ran. Set to -1 (default) if wish to run whole sequence",
    )
    args.add_argument(
        "--seq_from",
        type=int,
        default=0,
        help="Crop sequence from frame# when ran. Set to 0 (default) if wish to start from first frame",
    )
    args.add_argument(
        "--resultRoot",
        type=str,
        default="./Results",
        help="Directory to store trajectory and files generated by the script."
    )
    args.add_argument(
        "--useRR",
        action="store_true",
        help="Activate RerunVisualizer to generate <config.Project>.rrd file for visualization.",
    )
    args.add_argument(
        "--saveplt",
        action="store_true",
        help="Activate PLTVisualizer to generate <frame_idx>.jpg file in space folder for covariance visualization.",
    )
    args.add_argument(
        "--preload",
        action="store_true",
        help="Preload entier trajectory into RAM to reduce data fetching overhead during runtime."
    )
    args.add_argument(
        "--autoremove",
        action="store_true",
        help="Cleanup result sandbox after script finishs / crashed. Helpful during testing & debugging."
    )
    args.add_argument(
        "--noeval", 
        action="store_true",
        help="Evaluate sequence after running odometry."
    )
    args = args.parse_args()

    # Metadata setup & visualizer setup
    odomcfg, odomcfg_dict = load_config(Path(args.odom))
    odomcfg, odomcfg_dict = odomcfg.Odometry, odomcfg_dict["Odometry"]
    datacfg, datacfg_dict = load_config(Path(args.data))
    project_name = odomcfg.name + "@" + datacfg.name

    exp_space = Sandbox.create(Path(args.resultRoot), project_name)
    if args.autoremove: exp_space.set_autoremove()
    exp_space.config = {
        "Project": project_name,
        "Odometry": odomcfg_dict,
        "Data": {"args": datacfg_dict, "end_idx": args.seq_to, "start_idx": args.seq_from},
    }

    # Setup logging and visualization
    rrv.setup(project_name, True, Path(exp_space.folder, "rrvis.rrd"), useRR=args.useRR)
    
    if args.saveplt:
        PLTVisualizer.setup({
            PLTVisualizer.visualize_covaware_selector: PLTVisualizer.State.SAVE_FILE,
            PLTVisualizer.visualize_Obs: PLTVisualizer.State.SAVE_FILE,
            # PLTVisualizer.visualize_dpatch: PLTVisualizer.State.SAVE_FILE,
            # PLTVisualizer.visualize_depthcov: PLTVisualizer.State.SAVE_FILE,
            # PLTVisualizer.visualize_keypoint: PLTVisualizer.State.SAVE_FILE,
            # PLTVisualizer.visualize_stereo: PLTVisualizer.State.SAVE_FILE,
        }, save_path=Path(exp_space.folder), dpi=400)

    # Initialize data source
    sequence = GenericSequence.instantiate(**vars(datacfg)).clip(args.seq_from, args.seq_to)
    if args.preload:
        sequence = sequence.preload()
    
    system = MACVO.from_config(asNamespace(exp_space.config), sequence)
    system.receive_frames(sequence, exp_space, on_frame_finished=VisualizeRerunCallback)
    
    rrv.visualizeTrajectory(system.get_map())
    rrv.visualizePointCov(system.get_map())

    if not args.noeval:
        header, result = EvaluateSequences([str(exp_space.folder)], correct_scale=False)
        print_as_table(header, result)
