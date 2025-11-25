import sys
import os
import torch
import numpy as np
import pandas as pd

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from src.placement.placer_rl import (
    SwapRefineEnv, FullAssignEnv, PPOAgent, 
    GNNPolicy, AttentionPlacerPolicy, CongestionCNN,
    train_perturb_restore
)

def test_gnn_swap_refiner():
    print("Testing GNN Swap Refiner...")
    # Mock data
    batch_cells = ["c1", "c2", "c3"]
    placement_map = {
        "c1": (0.0, 0.0, 0),
        "c2": (10.0, 0.0, 1),
        "c3": (0.0, 10.0, 2)
    }
    sites_map = {
        0: (0.0, 0.0),
        1: (10.0, 0.0),
        2: (0.0, 10.0)
    }
    nets_map = {
        0: {"c1", "c2"},
        1: {"c2", "c3"}
    }
    fixed_pins = {}
    
    env = SwapRefineEnv(batch_cells, placement_map, sites_map, nets_map, fixed_pins, target_B=4)
    obs = env.reset()
    
    assert isinstance(obs, dict)
    assert "x" in obs
    assert "adj" in obs
    assert "mask" in obs
    
    print(f"Obs shapes: x={obs['x'].shape}, adj={obs['adj'].shape}, mask={obs['mask'].shape}")
    
    # Check GNN Policy
    node_feat_dim = obs['x'].shape[1]
    agent = PPOAgent(obs_dim=node_feat_dim, action_dim=(4, 4), policy_type="gnn", device="cpu")
    
    # Test forward
    action, logp, val = agent.get_action_and_value(obs)
    print(f"Action: {action}, LogP: {logp}, Val: {val}")
    
    # Test step
    obs, r, done = env.step(action)
    print(f"Step reward: {r}")
    
    # Test Perturb & Restore
    print("Testing Perturb & Restore...")
    train_perturb_restore(agent, lambda: SwapRefineEnv(batch_cells, placement_map, sites_map, nets_map, fixed_pins, target_B=4), num_episodes=2, swaps_per_episode=2)
    print("Perturb & Restore passed.")

def test_attention_full_placer():
    print("\nTesting Attention Full Placer...")
    # Mock data
    cells = ["c1", "c2"]
    sites_list = [(0, 0.0, 0.0), (1, 10.0, 0.0), (2, 0.0, 10.0), (3, 10.0, 10.0)]
    nets_map = {0: {"c1", "c2"}}
    fixed_pins = {}
    
    env = FullAssignEnv(cells, sites_list, nets_map, fixed_pins, max_action=4)
    obs = env.reset()
    
    assert isinstance(obs, dict)
    assert "cell" in obs
    assert "sites" in obs
    assert "map" in obs
    
    print(f"Obs shapes: cell={obs['cell'].shape}, sites={obs['sites'].shape}, map={obs['map'].shape}")
    
    # Check Attention Policy
    cell_dim = obs['cell'].shape[0]
    site_dim = obs['sites'].shape[1]
    agent = PPOAgent(obs_dim=(cell_dim, site_dim), action_dim=4, policy_type="attention", device="cpu")
    
    # Test forward
    action, logp, val = agent.get_action_and_value(obs)
    print(f"Action: {action}, LogP: {logp}, Val: {val}")
    
    # Test step
    obs, r, done = env.step(action)
    print(f"Step reward: {r}")

if __name__ == "__main__":
    test_gnn_swap_refiner()
    test_attention_full_placer()
    print("\nAll tests passed!")
