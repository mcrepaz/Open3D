# ----------------------------------------------------------------------------
# -                        Open3D: www.open3d.org                            -
# ----------------------------------------------------------------------------
# Copyright (c) 2018-2023 www.open3d.org
# SPDX-License-Identifier: MIT
# ----------------------------------------------------------------------------

# examples/python/t_reconstruction_system/dense_slam_gui.py

# P.S. This example is used in documentation, so, please ensure the changes are
# synchronized.

import open3d as o3d
import open3d.core as o3c
import open3d.visualization.gui as gui
import open3d.visualization.rendering as rendering

from config import ConfigParser

import os, sys
import numpy as np
import threading
import time
from common import load_rgbd_file_names, save_poses, load_intrinsic, extract_trianglemesh, get_default_dataset, \
    extract_rgbd_frames

import queue
import struct


def set_enabled(widget, enable):
    widget.enabled = enable
    for child in widget.get_children():
        child.enabled = enable


class ReconstructionWindow:

    def __init__(self, config, font_id, data_queue):

        self._data_queue = data_queue

        self.config = config

        self.window = gui.Application.instance.create_window(
            'Open3D - Reconstruction', 1280, 800)

        # Number of subsets
        self.number_subsets = 1
        self.dataset_count = 0
        self.dir_not_found = False

        w = self.window
        em = w.theme.font_size

        spacing = int(np.round(0.25 * em))
        vspacing = int(np.round(0.5 * em))

        margins = gui.Margins(vspacing)

        # First panel
        self.panel = gui.Vert(spacing, margins)

        ## Items in fixed props
        self.fixed_prop_grid = gui.VGrid(2, spacing, gui.Margins(em, 0, em, 0))

        ### Depth scale slider
        scale_label = gui.Label('Depth scale')
        self.scale_slider = gui.Slider(gui.Slider.INT)
        self.scale_slider.set_limits(1000, 5000)
        self.scale_slider.int_value = int(config.depth_scale)
        self.fixed_prop_grid.add_child(scale_label)
        self.fixed_prop_grid.add_child(self.scale_slider)

        voxel_size_label = gui.Label('Voxel size')
        self.voxel_size_slider = gui.Slider(gui.Slider.DOUBLE)
        self.voxel_size_slider.set_limits(0.003, 0.01)
        self.voxel_size_slider.double_value = config.voxel_size
        self.fixed_prop_grid.add_child(voxel_size_label)
        self.fixed_prop_grid.add_child(self.voxel_size_slider)

        trunc_multiplier_label = gui.Label('Trunc multiplier')
        self.trunc_multiplier_slider = gui.Slider(gui.Slider.DOUBLE)
        self.trunc_multiplier_slider.set_limits(1.0, 20.0)
        self.trunc_multiplier_slider.double_value = config.trunc_voxel_multiplier
        self.fixed_prop_grid.add_child(trunc_multiplier_label)
        self.fixed_prop_grid.add_child(self.trunc_multiplier_slider)

        est_block_count_label = gui.Label('Est. blocks')
        self.est_block_count_slider = gui.Slider(gui.Slider.INT)
        self.est_block_count_slider.set_limits(40000, 100000)
        self.est_block_count_slider.int_value = config.block_count
        self.fixed_prop_grid.add_child(est_block_count_label)
        self.fixed_prop_grid.add_child(self.est_block_count_slider)

        est_point_count_label = gui.Label('Est. points')
        self.est_point_count_slider = gui.Slider(gui.Slider.INT)
        self.est_point_count_slider.set_limits(500000, 8000000)
        self.est_point_count_slider.int_value = config.est_point_count
        self.fixed_prop_grid.add_child(est_point_count_label)
        self.fixed_prop_grid.add_child(self.est_point_count_slider)

        ## Items in adjustable props
        self.adjustable_prop_grid = gui.VGrid(2, spacing,
                                              gui.Margins(em, 0, em, 0))

        ### Reconstruction interval
        interval_label = gui.Label('Recon. interval')
        self.interval_slider = gui.Slider(gui.Slider.INT)
        self.interval_slider.set_limits(1, 500)
        self.interval_slider.int_value = 50
        self.adjustable_prop_grid.add_child(interval_label)
        self.adjustable_prop_grid.add_child(self.interval_slider)

        ### Depth max slider
        max_label = gui.Label('Depth max')
        self.max_slider = gui.Slider(gui.Slider.DOUBLE)
        self.max_slider.set_limits(3.0, 6.0)
        self.max_slider.double_value = config.depth_max
        self.adjustable_prop_grid.add_child(max_label)
        self.adjustable_prop_grid.add_child(self.max_slider)

        ### Depth diff slider
        diff_label = gui.Label('Depth diff')
        self.diff_slider = gui.Slider(gui.Slider.DOUBLE)
        self.diff_slider.set_limits(0.07, 0.5)
        self.diff_slider.double_value = config.odometry_distance_thr
        self.adjustable_prop_grid.add_child(diff_label)
        self.adjustable_prop_grid.add_child(self.diff_slider)

        ### Update surface?
        update_label = gui.Label('Update surface?')
        self.update_box = gui.Checkbox('')
        self.update_box.checked = True
        self.adjustable_prop_grid.add_child(update_label)
        self.adjustable_prop_grid.add_child(self.update_box)

        ### Ray cast color?
        raycast_label = gui.Label('Raycast color?')
        self.raycast_box = gui.Checkbox('')
        self.raycast_box.checked = True
        self.adjustable_prop_grid.add_child(raycast_label)
        self.adjustable_prop_grid.add_child(self.raycast_box)

        set_enabled(self.fixed_prop_grid, True)

        ## Application control
        b = gui.ToggleSwitch('Resume/Pause')
        b.set_on_clicked(self._on_switch)

        ## Tabs
        tab_margins = gui.Margins(0, int(np.round(0.5 * em)), 0, 0)
        tabs = gui.TabControl()

        ### Input image tab
        tab1 = gui.Vert(0, tab_margins)
        self.input_color_image = gui.ImageWidget()
        self.input_depth_image = gui.ImageWidget()
        tab1.add_child(self.input_color_image)
        tab1.add_fixed(vspacing)
        tab1.add_child(self.input_depth_image)
        tabs.add_tab('Input images', tab1)

        ### Rendered image tab
        tab2 = gui.Vert(0, tab_margins)
        self.raycast_color_image = gui.ImageWidget()
        self.raycast_depth_image = gui.ImageWidget()
        tab2.add_child(self.raycast_color_image)
        tab2.add_fixed(vspacing)
        tab2.add_child(self.raycast_depth_image)
        tabs.add_tab('Raycast images', tab2)

        ### Info tab
        tab3 = gui.Vert(0, tab_margins)
        self.output_info = gui.Label('Output info')
        self.output_info.font_id = font_id
        tab3.add_child(self.output_info)
        tabs.add_tab('Info', tab3)

        self.panel.add_child(gui.Label('Starting settings'))
        self.panel.add_child(self.fixed_prop_grid)
        self.panel.add_fixed(vspacing)
        self.panel.add_child(gui.Label('Reconstruction settings'))
        self.panel.add_child(self.adjustable_prop_grid)
        self.panel.add_child(b)
        self.panel.add_stretch()
        self.panel.add_child(tabs)

        # Scene widget
        self.widget3d = gui.SceneWidget()

        # FPS panel
        self.fps_panel = gui.Vert(spacing, margins)
        self.output_fps = gui.Label('FPS: 0.0')
        self.fps_panel.add_child(self.output_fps)

        # Now add all the complex panels
        w.add_child(self.panel)
        w.add_child(self.widget3d)
        w.add_child(self.fps_panel)

        self.widget3d.scene = rendering.Open3DScene(self.window.renderer)
        self.widget3d.scene.set_background([1, 1, 1, 1])

        w.set_on_layout(self._on_layout)
        w.set_on_close(self._on_close)



        # Start running
        threading.Thread(name='UpdateMain', target=self.update_main).start()


    def _on_layout(self, ctx):
        em = ctx.theme.font_size

        panel_width = 20 * em
        rect = self.window.content_rect

        self.panel.frame = gui.Rect(rect.x, rect.y, panel_width, rect.height)

        x = self.panel.frame.get_right()
        self.widget3d.frame = gui.Rect(x, rect.y,
                                       rect.get_right() - x, rect.height)

        fps_panel_width = 7 * em
        fps_panel_height = 2 * em
        self.fps_panel.frame = gui.Rect(rect.get_right() - fps_panel_width,
                                        rect.y, fps_panel_width,
                                        fps_panel_height)

    # Toggle callback: application's main controller
    def _on_switch(self, is_on):
        # if not self.is_started:
        #     gui.Application.instance.post_to_main_thread(
        #         self.window, self._on_start)
        self.is_running = not self.is_running

    # On start: point cloud buffer and model initialization.
    def _on_start(self):
        max_points = self.est_point_count_slider.int_value

        pcd_placeholder = o3d.t.geometry.PointCloud(
            o3c.Tensor(np.zeros((max_points, 3), dtype=np.float32)))
        pcd_placeholder.point.colors = o3c.Tensor(
            np.zeros((max_points, 3), dtype=np.float32))
        mat = rendering.MaterialRecord()
        mat.shader = 'defaultUnlit'
        mat.sRGB_color = True
        self.widget3d.scene.scene.add_geometry('points', pcd_placeholder, mat)

        self.model = o3d.t.pipelines.slam.Model(
            self.voxel_size_slider.double_value, 16,
            self.est_block_count_slider.int_value, o3c.Tensor(np.eye(4)),
            o3c.Device(self.config.device))
        self.is_started = True

        set_enabled(self.fixed_prop_grid, False)
        set_enabled(self.adjustable_prop_grid, True)

    def _on_close(self):
        self.is_done = True

        if self.is_started:
            print('Saving model to {}...'.format(config.path_npz))
            self.model.voxel_grid.save(config.path_npz)
            print('Finished.')

            mesh_fname = '.'.join(config.path_npz.split('.')[:-1]) + '.ply'
            print('Extracting and saving mesh to {}...'.format(mesh_fname))
            mesh = extract_trianglemesh(self.model.voxel_grid, config,
                                        mesh_fname)
            print('Finished.')

            log_fname = '.'.join(config.path_npz.split('.')[:-1]) + '.log'
            print('Saving trajectory to {}...'.format(log_fname))
            save_poses(log_fname, self.poses)
            print('Finished.')

        return True

    def init_render(self, depth_ref, color_ref):
        self.input_depth_image.update_image(
            depth_ref.colorize_depth(float(self.scale_slider.int_value),
                                     config.depth_min,
                                     self.max_slider.double_value).to_legacy())
        self.input_color_image.update_image(color_ref.to_legacy())

        self.raycast_depth_image.update_image(
            depth_ref.colorize_depth(float(self.scale_slider.int_value),
                                     config.depth_min,
                                     self.max_slider.double_value).to_legacy())
        self.raycast_color_image.update_image(color_ref.to_legacy())
        self.window.set_needs_layout()

        bbox = o3d.geometry.AxisAlignedBoundingBox([-5, -5, -5], [5, 5, 5])
        self.widget3d.setup_camera(60, bbox, [0, 0, 0])
        self.widget3d.look_at([0, 0, 0], [0, -1, -3], [0, -1, 0])

    def update_render(self, input_depth, input_color, raycast_depth,
                      raycast_color, pcd, frustum):
        self.input_depth_image.update_image(
            input_depth.colorize_depth(
                float(self.scale_slider.int_value), config.depth_min,
                self.max_slider.double_value).to_legacy())
        self.input_color_image.update_image(input_color.to_legacy())

        self.raycast_depth_image.update_image(
            raycast_depth.colorize_depth(
                float(self.scale_slider.int_value), config.depth_min,
                self.max_slider.double_value).to_legacy())
        self.raycast_color_image.update_image(
            (raycast_color).to(o3c.uint8, False, 255.0).to_legacy())

        if self.is_scene_updated:
            if pcd is not None and pcd.point.positions.shape[0] > 0:
                self.widget3d.scene.scene.update_geometry(
                    'points', pcd, rendering.Scene.UPDATE_POINTS_FLAG |
                                   rendering.Scene.UPDATE_COLORS_FLAG)



        self.widget3d.scene.remove_geometry("frustum")
        mat = rendering.MaterialRecord()
        mat.shader = "unlitLine"
        mat.line_width = 5.0
        self.widget3d.scene.add_geometry("frustum", frustum, mat)

    def save_mesh_as_triangles(self, mesh, file_name):
        triangles = mesh.triangle['indices'].numpy()
        vertices = mesh.vertex['positions'].numpy()
        colors = mesh.vertex['colors'].numpy()

        print(f'starting vertices: {len(triangles)}')
        print(f'starting triangles: {len(vertices)}')
        print(f'starting colors: {len(colors)}')

        # Get the unique indices of vertices referenced by triangles
        referenced_vertex_indices = np.unique(triangles)

        # Extract the vertices referenced by the faces
        referenced_vertices = vertices[referenced_vertex_indices]
        # Extract the colors referenced by the faces
        referenced_colors = colors[referenced_vertex_indices]

        # Find the indices of the referenced vertices within referenced_vertex_indices
        indices_in_referenced = np.searchsorted(referenced_vertex_indices, triangles)
        # Create the ndarray for new_mesh.triangles
        referenced_triangles = np.column_stack(
            (indices_in_referenced[:, 0], indices_in_referenced[:, 1], indices_in_referenced[:, 2]))

        # unique_vertices = np.unique(vertices, axis=0)
        # print(f"Original vertices: {len(vertices)}")
        # print(f"Unique vertices: {len(unique_vertices)}")
        #
        # referenced_vertices = np.unique(triangles)
        # print(f'mesh triangles {len(triangles)}')
        # print(f"Referenced vertices: {len(referenced_vertices)}")
        #
        # used_vertices = np.isin(np.arange(len(vertices)), referenced_vertices)
        # unused_vertices = np.sum(~used_vertices)
        # print(f"Unused vertices: {unused_vertices}")

        # mesh_legacy = mesh.to_legacy()
        # triangles_legacy = mesh_legacy.triangle['indices'].numpy()
        # vertices_legacy = mesh_legacy.vertex['positions'].numpy()

        # Save referenced_vertices to CSV
        print(f'ref vertices: {len(referenced_vertices)}')
        print(f'ref triangles: {len(referenced_triangles)}')
        print(f'ref colors: {len(referenced_colors)}')

        np.savetxt(f'{file_name}_referenced_vertices.csv', referenced_vertices, delimiter=',')
        print(f'Referenced vertices saved to {file_name}_referenced_vertices.csv')

        np.savetxt(f'{file_name}_referenced_triangles.csv', referenced_triangles, delimiter=',', fmt='%d')
        print(f'referenced triangles saved to {file_name}_referenced_triangles.csv')

        np.savetxt(f'{file_name}_referenced_colors.csv', referenced_colors, delimiter=',')
        print(f'referenced colors saved to {file_name}_referenced_colors.csv')

    # Major loop
    def update_main(self):
        T_frame_to_model = o3c.Tensor(np.identity(4))

        print("\n--- Start time recording... ---\n")
        start_time = time.time()

        print("Start loop...")
        #for i in range(self.number_subsets):
        while True:

            self.config.path_dataset = '/home/martin/Desktop/Open3D/examples/python/t_reconstruction_system/realsense-dataset/scene'
            self.config.path_dataset += '_' + str(self.dataset_count+1) + '/'

            if not os.path.exists(self.config.path_dataset):
                print(f"Dataset folder {self.config.path_dataset} does not exist.")
                time.sleep(5)
                if self.dir_not_found:
                    break
                else:
                    self.dir_not_found = True
                    print("Exit")
                    continue

            print("\nStart dataset #" + str(self.dataset_count))
            self.is_done = False

            self.is_started = False
            self.is_running = False
            self.is_surface_updated = False

            self.idx = 0
            self.poses = []

            gui.Application.instance.post_to_main_thread(self.window, self._on_start)

            self.is_running = True


            #print("Before load_rgbd_file_names in dense_slam_gui_modified.py")
            depth_file_names, color_file_names = load_rgbd_file_names(self.config)
            #print("After load_rgbd_file_names in dense_slam_gui_modified.py")
            intrinsic = load_intrinsic(self.config)

            n_files = len(color_file_names)
            print(f'n_files {n_files}')
            device = o3d.core.Device(config.device)

            depth_ref = o3d.t.io.read_image(depth_file_names[0])
            color_ref = o3d.t.io.read_image(color_file_names[0])
            input_frame = o3d.t.pipelines.slam.Frame(depth_ref.rows,
                                                     depth_ref.columns, intrinsic,
                                                     device)
            raycast_frame = o3d.t.pipelines.slam.Frame(depth_ref.rows,
                                                       depth_ref.columns, intrinsic,
                                                       device)

            input_frame.set_data_from_image('depth', depth_ref)
            input_frame.set_data_from_image('color', color_ref)

            raycast_frame.set_data_from_image('depth', depth_ref)
            raycast_frame.set_data_from_image('color', color_ref)

            gui.Application.instance.post_to_main_thread(
                self.window, lambda: self.init_render(depth_ref, color_ref))

            fps_interval_len = 30
            self.idx = 0
            pcd = None

            start = time.time()
            while not self.is_done:
                if not self.is_started or not self.is_running:
                    time.sleep(0.01)
                    continue

                depth = o3d.t.io.read_image(depth_file_names[self.idx]).to(device)
                color = o3d.t.io.read_image(color_file_names[self.idx]).to(device)

                input_frame.set_data_from_image('depth', depth)
                input_frame.set_data_from_image('color', color)

                try:
                    if self.idx > 0:
                        #print(self.idx)
                        result = self.model.track_frame_to_model(
                            input_frame,
                            raycast_frame,
                            float(self.scale_slider.int_value),
                            self.max_slider.double_value,
                        )
                        T_frame_to_model = T_frame_to_model @ result.transformation

                    self.poses.append(T_frame_to_model.cpu().numpy())
                    self.model.update_frame_pose(self.idx, T_frame_to_model)
                    self.model.integrate(input_frame,
                                         float(self.scale_slider.int_value),
                                         self.max_slider.double_value,
                                         self.trunc_multiplier_slider.double_value)
                    self.model.synthesize_model_frame(
                        raycast_frame, float(self.scale_slider.int_value),
                        config.depth_min, self.max_slider.double_value,
                        self.trunc_multiplier_slider.double_value,
                        self.raycast_box.checked)

                    if (self.idx % self.interval_slider.int_value == 0 and
                        self.update_box.checked) \
                            or (self.idx == n_files - 1):
                        pcd = self.model.voxel_grid.extract_point_cloud(
                            3.0, self.est_point_count_slider.int_value).to(
                            o3d.core.Device('CPU:0'))

                        # if pcd is not None and pcd.point.positions.shape[0] > 0:
                        # if self.idx >= 10:
                        # mesh = self.model.voxel_grid.extract_triangle_mesh(3.0, self.est_point_count_slider.int_value).to(
                        #           o3d.core.Device('CPU:0'))
                        # # #     # o3d.io.write_point_cloud("point.ply", pcd.to_legacy())
                        # #      o3d.io.write_triangle_mesh(f'mesh_{self.idx}.obj', mesh.to_legacy(), False, True)
                        # #     # o3d.visualization.draw([mesh.to_legacy()])
                        # self._data_queue.put(mesh)
                        #     # self.save_mesh_as_triangles(mesh, 'living_room')

                        self.is_scene_updated = True
                    else:
                        self.is_scene_updated = False

                    frustum = o3d.geometry.LineSet.create_camera_visualization(
                        color.columns, color.rows, intrinsic.numpy(),
                        np.linalg.inv(T_frame_to_model.cpu().numpy()), 0.2)
                    frustum.paint_uniform_color([0.961, 0.475, 0.000])

                    # Output FPS
                    if (self.idx % fps_interval_len == 0):
                        end = time.time()
                        elapsed = end - start
                        start = time.time()
                        self.output_fps.text = 'FPS: {:.3f}'.format(fps_interval_len /
                                                                    elapsed)

                    # Output info
                    info = 'Frame {}/{}\n\n'.format(self.idx, n_files)
                    info += 'Transformation:\n{}\n'.format(
                        np.array2string(T_frame_to_model.numpy(),
                                        precision=3,
                                        max_line_width=40,
                                        suppress_small=True))
                    info += 'Active voxel blocks: {}/{}\n'.format(
                        self.model.voxel_grid.hashmap().size(),
                        self.model.voxel_grid.hashmap().capacity())
                    info += 'Surface points: {}/{}\n'.format(
                        0 if pcd is None else pcd.point.positions.shape[0],
                        self.est_point_count_slider.int_value)

                    self.output_info.text = info

                    # print(pcd.point['positions'].shape)
                    gui.Application.instance.post_to_main_thread(
                        self.window, lambda: self.update_render(
                            input_frame.get_data_as_image('depth'),
                            input_frame.get_data_as_image('color'),
                            raycast_frame.get_data_as_image('depth'),
                            raycast_frame.get_data_as_image('color'), pcd, frustum))
                except Exception as e:
                    print(f"Tracking failed at frame {self.idx}: {e}")

                self.idx += 1
                self.is_done = self.is_done | (self.idx >= n_files)

            mesh = self.model.voxel_grid.extract_triangle_mesh(3.0, self.est_point_count_slider.int_value).to(
                o3d.core.Device('CPU:0'))
            self._data_queue.put(mesh)


            # mesh = self.model.voxel_grid.extract_triangle_mesh(3.0, self.est_point_count_slider.int_value).to(
            #     o3d.core.Device('CPU:0'))
            # o3d.io.write_triangle_mesh(f'mesh_{i}_{self.idx}.obj', mesh.to_legacy(), False, True)
            # print(f"saved mesh_{i}_{self.idx}.obj")

            # time.sleep(0.5)

            self.dataset_count += 1

        print("\n--- Stop time recording... ---\n")
        end_time = time.time()
        total_time = end_time - start_time
        print(f"Total time: {total_time:.2f} sec")


def process_mesh(mesh, file_name):
    triangles = mesh.triangle['indices'].numpy()
    vertices = mesh.vertex['positions'].numpy()
    colors = mesh.vertex['colors'].numpy()

    # print(f'starting vertices: {len(triangles)}')
    # print(f'starting triangles: {len(vertices)}')
    # print(f'starting colors: {len(colors)}')

    # Get the unique indices of vertices referenced by triangles
    referenced_vertex_indices = np.unique(triangles)

    # Extract the vertices referenced by the faces
    referenced_vertices = vertices[referenced_vertex_indices]
    # Extract the colors referenced by the faces
    referenced_colors = colors[referenced_vertex_indices]

    # Find the indices of the referenced vertices within referenced_vertex_indices
    indices_in_referenced = np.searchsorted(referenced_vertex_indices, triangles)
    # Create the ndarray for new_mesh.triangles
    referenced_triangles = np.column_stack(
        (indices_in_referenced[:, 0], indices_in_referenced[:, 1], indices_in_referenced[:, 2])).astype(np.int32)

    print(f'ref vertices: {len(referenced_vertices)}')
    print(f'ref triangles: {len(referenced_triangles)}')
    print(f'ref colors: {len(referenced_colors)}')

    # np.savetxt(f'{file_name}_referenced_vertices.csv', referenced_vertices, delimiter=',')
    # print(f'Referenced vertices saved to {file_name}_referenced_vertices.csv')
    #
    # np.savetxt(f'{file_name}_referenced_triangles.csv', referenced_triangles, delimiter=',', fmt='%d')
    # print(f'referenced triangles saved to {file_name}_referenced_triangles.csv')
    #
    # np.savetxt(f'{file_name}_referenced_colors.csv', referenced_colors, delimiter=',')
    # print(f'referenced colors saved to {file_name}_referenced_colors.csv')

    len_vertices_byte = referenced_vertices.nbytes
    len_triangles_byte = referenced_triangles.nbytes
    len_colors_byte = referenced_colors.nbytes

    message_length = 16 + len_vertices_byte + len_triangles_byte + len_colors_byte  # 4 + 4 + 4 + 4, size of int used to specify the lengths and toto lengths

    print(f'len byte vertices: {len_vertices_byte}')
    print(f'len byte triangles: {len_triangles_byte}')
    print(f'len byte colors: {len_colors_byte}')
    print(f'len byte array: {message_length}')

    # Concatenate string and tuple bytes
    byte_array = struct.pack('<i', message_length) + \
                 struct.pack('<i', len_vertices_byte) + referenced_vertices.tobytes() + \
                 struct.pack('<i', len_triangles_byte) + referenced_triangles.tobytes() + \
                 struct.pack('<i', len_colors_byte) + referenced_colors.tobytes()
    # byte_array = referenced_triangles.tobytes()

    # Create the format string
    # format_string = 'i' * len(referenced_triangles) * 3
    # format_string = 'i' + 'i' + 'f' * len(referenced_vertices) * 3 + 'i' + 'i' * len(referenced_triangles) * 3

    # Unpack the byte array
    # unpacked_data = struct.unpack(format_string, byte_array)
    # print(unpacked_data)

    return byte_array


def process_data(data_queue):
    import socket
    # Create a TCP/IP socket
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Connect to the server
    server_address = ('localhost', 65432)  # Adjust the address and port as needed
    client_socket.connect(server_address)

    tot_size = 0
    index = 0
    while True:
        try:
            data = None
            data = data_queue.get()
            if data is not None:
                byte_array = process_mesh(data, str(index))

                client_socket.sendall(byte_array)

                index += 1

                # tot_size += len(byte_array)
                # print("tot_size " + str(len(byte_array)))

        except Exception as e:
            print("Error processing data:", e)


if __name__ == '__main__':
    parser = ConfigParser()
    parser.add(
        '--config',
        is_config_file=True,
        help='YAML config file path. Please refer to default_config.yml as a '
             'reference. It overrides the default config file, but will be '
             'overridden by other command line inputs.')
    parser.add('--default_dataset',
               help='Default dataset is used when config file is not provided. '
                    'Default dataset may be selected from the following options: '
                    '[lounge, bedroom, jack_jack]',
               default='lounge')
    parser.add('--path_npz',
               help='path to the npz file that stores voxel block grid.',
               default='output.npz')
    config = parser.get_config()

    if config.path_dataset == '':
        config = get_default_dataset(config)

    # Extract RGB-D frames and intrinsic from bag file.
    if config.path_dataset.endswith(".bag"):
        assert os.path.isfile(
            config.path_dataset), (f"File {config.path_dataset} not found.")
        print("Extracting frames from RGBD video file")
        config.path_dataset, config.path_intrinsic, config.depth_scale = extract_rgbd_frames(
            config.path_dataset)

    # FIFO queue
    data_queue = queue.Queue()

    # create thread to send mesh over TCP socket
    socket_thread = threading.Thread(target=process_data, args=(data_queue,))
    socket_thread.daemon = True
    socket_thread.start()

    app = gui.Application.instance
    app.initialize()
    mono = app.add_font(gui.FontDescription(gui.FontDescription.MONOSPACE))
    w = ReconstructionWindow(config, mono, data_queue)
    app.run()