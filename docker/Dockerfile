FROM ros:humble

# RMW ZENOH experimental flag
ARG EXPERIMENTAL_ZENOH_RMW=TRUE

# Set noninteractive mode for APT
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    locales \
    curl \
    git \
    && locale-gen en_US.UTF-8 \
    && update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8

# Install dependencies
RUN apt-get update -q && \
    apt-get install -yq --no-install-recommends \
    wget \ 
    software-properties-common \ 
    python3-pip \
    python-is-python3 \
    python3-argcomplete \
    python3-colcon-common-extensions \
    python3-colcon-mixin \
    python3-rosdep \
    libpython3-dev \
    python3-tk \
    qttools5-dev \
    ros-humble-desktop \
    ros-dev-tools \
    ros-humble-tf2-tools \
    tmux \
    # check if Zenoh should be installed
    $(if [ "$EXPERIMENTAL_ZENOH_RMW" = "TRUE" ]; then echo "ros-humble-rmw-zenoh-cpp"; fi) \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --upgrade pip

RUN pip3 install --no-cache-dir \
    "numpy<2.0" \
    "setuptools>=71.0.0,<80.0.0" \
    "opencv-python-headless<4.12"


RUN pip3 install --no-cache-dir "lerobot==0.4.4" --ignore-installed sympy

# Create workspace
RUN mkdir -p /ros2_ws/src
COPY ./src /ros2_ws/src
WORKDIR /ros2_ws/

RUN rosdep update && apt-get update && rosdep install --from-paths src --ignore-src -r -y
RUN cd src/spot_ros2/ && ./install_spot_ros2.sh

# Source workspace on container startup
RUN echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc

# Boston dynamics packages
RUN pip3 install --no-cache-dir \
    bosdyn-client \
    bosdyn-mission \
    bosdyn-api \
    bosdyn-choreography-client \
    aiortc

RUN pip3 install --no-cache-dir --force-reinstall \
    "numpy<2.0" \
    "setuptools>=71.0.0,<80.0.0"

CMD ["/bin/bash"]