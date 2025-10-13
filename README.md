# Spot ROS2 Multi-Robot Workspace

A comprehensive ROS2 workspace for multi-robot control of Boston Dynamics Spot robots with advanced localization, point cloud processing, and distributed communication capabilities.

## Overview

This workspace integrates multiple robotics components to enable coordinated control of multiple Spot robots:

- **Multi-Spot Control**: Coordinate multiple Boston Dynamics Spot robots
- **Fiducial-Based Localization**: GraphNav with ICP-enhanced point cloud registration
- **Distributed Communication**: Zenoh-based networking for robot coordination
- **Docker Support**: Containerized deployment for consistent environments
- **Unity Integration**: ROS# communication bridge for visualization and simulation

## Architecture

The system consists of several key components:

1. **Spot ROS2**: Official Boston Dynamics ROS2 driver for Spot robots
2. **Spot Multi**: Custom multi-robot coordination package with ICP localization
3. **Small GICP**: Fast point cloud registration library
4. **ROS Sharp**: Communication bridge between ROS2 and Unity/C# applications
5. **Zenoh Router**: Distributed networking for multi-robot communication

## Quick Start with Docker

### Prerequisites

- Docker and Docker Compose
- Valid Spot robot credentials
- Network access to Spot robots

### 1. Clone the Repository

```bash
git clone --recursive <repository-url>
cd spot_ros2_multi_ws
```

### 2. Configure Robot Credentials

Create configuration files in the `secrets/` directory:

```bash
# Copy example configs and edit with your robot details
cp src/spot_multi/configs/spot_gouger.yaml secrets/
cp src/spot_multi/configs/spot_tusker.yaml secrets/
```

Edit the YAML files with your robot IP addresses, usernames, and passwords.

### 3. Launch the Docker

```bash
# Start all services
docker-compose up -d

# Access the ROS2 container
docker exec -it ros2_ws bash

# Inside container, source and launch
source install/setup.bash
```

### 3. Install Dependencies During the first time

```bash
# Install ROS2 dependencies
rosdep install --from-paths src --ignore-src -r -y

# build the package
colcon build --symlink-install

# launch these commands
./launch_multi_spot.sh
```

## Manual Installation

### Prerequisites

- Ubuntu 22.04 with ROS2 Humble
- Python 3.8+
- Git with submodule support

### 1. Initialize Workspace

```bash
mkdir -p ~/spot_ros2_multi_ws/src
cd ~/spot_ros2_multi_ws
```

### 2. Clone with Submodules

```bash
git clone --recursive <repository-url> .
```

### 3. Install Dependencies

```bash
# Install ROS2 dependencies
rosdep install --from-paths src --ignore-src -r -y

# Install Python dependencies
cd src/spot_ros2/ && ./install_spot_ros2.sh
```

### 4. Build Workspace

```bash
colcon build --symlink-install
```

### 5. Launch
```bash
./launch_multi_spot.sh
```
will launch everything in a tmux workspace. To learn about each command, read below.

## Configuration

### Robot Configuration Files

Place robot-specific configuration files in `~/spot_configs/` in the container or `secrets/` directory under host machine if using docker.

### Zenoh Network Configuration
The Zenoh router is still under progress.

Edit `zenoh-config.json5` to configure distributed networking:

```json5
{
  "mode": "router",
  "connect": {
    "endpoints": ["tcp/your-central-router:7447"]
  },
  "listen": {
    "endpoints": ["tcp/0.0.0.0:7447", "udp/0.0.0.0:7447"]
  }
}
```

## Usage

### Multi-Robot Launch

#### Terminal 1: First Spot Robot
```bash
ros2 launch spot_driver spot_driver.launch.py config_file:=$HOME/spot_configs/spot_gouger.yaml
```

#### Terminal 2: Second Spot Robot
```bash
ros2 launch spot_driver spot_driver.launch.py config_file:=$HOME/spot_configs/spot_tusker.yaml
```

#### Terminal 3: ROS# Communication Bridge
```bash
ros2 launch file_server2 ros_sharp_communication.launch.py
```

#### Terminal 4: Multi-Robot Coordination
```bash
ros2 launch spot_multi spot_multi.launch.py
```

### Key Topics

- `/multi_spot/cmd_vel` - Coordinated velocity commands
- `/spot_tf_compute` - ICP-enhanced localization
- `/gouger/lidar/points` - Point cloud data from Gouger
- `/tusker/lidar/points` - Point cloud data from Tusker


## Development

### Building Individual Packages

```bash
# Build only spot_multi package
colcon build --packages-select spot_multi

# Build with debug symbols
colcon build --cmake-args -DCMAKE_BUILD_TYPE=Debug
```

## Troubleshooting

### Common Issues

1. **Robot Connection Failed**
   - Verify IP addresses in configuration files
   - Check network connectivity to robots
   - Confirm credentials are correct

2. **ICP Registration Poor**
   - Ensure sufficient point cloud overlap
   - Check velodyne sensor configuration
   - Verify robot positioning

3. **Unity Communication Issues**
   - Confirm ROS# bridge is running
   - Check firewall settings
   - Verify network ports are open

### Logs and Debugging

```bash
# View ROS2 logs
ros2 log list
ros2 log view <node_name>

# Docker logs
docker-compose logs ros2_ws
docker-compose logs zenoh_router
```

## License

This project integrates multiple open-source components. See individual package licenses in their respective directories.

