import time
import dgl
import torch
import torch.nn.functional as F
from torch.utils.data import Dataset
from rdkit import Chem 
from rdkit import RDPaths
import csv
from dgllife.utils import smiles_to_complete_graph

from ogb.graphproppred import DglGraphPropPredDataset, Evaluator

from scipy import sparse as sp
import numpy as np
import networkx as nx
from tqdm import tqdm
from rdkit import Chem
from rdkit.Chem.rdchem import HybridizationType
from rdkit.Chem.rdchem import BondType as BT
from rdkit.Chem import AllChem
import math
import random


ATOM_LIST = list(range(1,119))

CHIRALITY_LIST = [
    Chem.rdchem.ChiralType.CHI_UNSPECIFIED,
    Chem.rdchem.ChiralType.CHI_TETRAHEDRAL_CW,
    Chem.rdchem.ChiralType.CHI_TETRAHEDRAL_CCW,
    Chem.rdchem.ChiralType.CHI_OTHER
]

BOND_LIST = [
    BT.SINGLE, 
    BT.DOUBLE, 
    BT.TRIPLE, 
    BT.AROMATIC
]

BONDDIR_LIST = [
    Chem.rdchem.BondDir.NONE,
    Chem.rdchem.BondDir.ENDUPRIGHT,
    Chem.rdchem.BondDir.ENDDOWNRIGHT
]

def read_smiles(data_path):
    smiles_data = []
    with open(data_path) as csv_file:
        csv_reader = csv.reader(csv_file, delimiter = ',')
        for i, row in enumerate(csv_reader):
            smiles = row[-1]
            smiles_data.append(smiles)
    
    return smiles_data
            
def featurize_atoms(mol):
    atomic_number = []
    chirality_idx = []
    type_idx = []
    for atom in mol.GetAtoms():
        atomic_number.append(atom.GetAtomicNum())
        chirality_idx.append(CHIRALITY_LIST.index(atom.GetChiralTag()))
        type_idx.append(ATOM_LIST.index(atom.GetAtomicNum))
    
    x1 = torch.tensor(type_idx,dtype = torch.long).view(-1,1)
    x2 = torch.tensor(chirality_idx, dtype = torch.long).view(-1,1)
    x = torch.cat([x1,x2],dim = -1)
        
    return {'atomic': x}

def featurize_edges(mol,add_self_loop = False):
    row, col, edge_feat = [], [], []
    for bond in mol.GetBonds():
        start, end = bond.GetBeginAtomIdx(), bond.GetEndAtomIdx()
        row += [start, end]
        col += [end, start]
        edge_feat.append([
            BOND_LIST.index(bond.GetBondType()),
            BONDDIR_LIST.index(bond.GetBondDir())
        ])
        edge_feat.append([
            BOND_LIST.index(bond.GetBondType()),
            BONDDIR_LIST.index(bond.GetBondDir())
        ])

    edge_index = torch.tensor([row, col], dtype=torch.long)
    edge_attr = torch.tensor(np.array(edge_feat), dtype=torch.long).reshape(-1,1)
    
    return {'type': edge_attr}

    
        


class MoleculeDataset(Dataset):
    def __init__(self,data_path):
        super(Dataset,self).__init__()
        self.smiles_data = read_smiles(data_path)
    
    def __getitem__(self,index):
        mol = Chem.MolFromSmiles(self.smiles_data[index])
        
        N = mol.GetNumAtoms()
        M = mol.GetNumBonds()
        
        num_mask_nodes = max([1, math.floor(0.25*N)])
        num_mask_edges = max([0, math.floor(0.25*M)])
        mask_nodes_i = random.sample(list(range(N)), num_mask_nodes)
        mask_nodes_j = random.sample(list(range(N)), num_mask_nodes)
        mask_edges_i_single = random.sample(list(range(M)), num_mask_edges)
        mask_edges_j_single = random.sample(list(range(M)), num_mask_edges)
        mask_edges_i = [2*i for i in mask_edges_i_single] + [2*i+1 for i in mask_edges_i_single]
        mask_edges_j = [2*i for i in mask_edges_j_single] + [2*i+1 for i in mask_edges_j_single]
        
        


# class OGBMOLDGL(torch.utils.data.Dataset):
#     def __init__(self, data, split):
#         self.split = split
#         self.data = [g for g in data[self.split]]
#         self.graph_lists = []
#         self.graph_labels = []
#         for g in self.data:
#             if g[0].number_of_nodes() > 5:
#                 self.graph_lists.append(g[0])
#                 self.graph_labels.append(g[1])
#         self.n_samples = len(self.graph_lists)

#     def __len__(self):
#         """Return the number of graphs in the dataset."""
#         return self.n_samples

#     def __getitem__(self, idx):
#         """
#             Get the idx^th sample.
#             Parameters
#             ---------
#             idx : int
#                 The sample index.
#             Returns
#             -------
#             (dgl.DGLGraph, int)
#                 DGLGraph with node feature stored in `feat` field
#                 And its label.
#         """
#         return self.graph_lists[idx], self.graph_labels[idx]

def add_eig_vec(g, pos_enc_dim):
    """
     Graph positional encoding v/ Laplacian eigenvectors
     This func is for eigvec visualization, same code as positional_encoding() func,
     but stores value in a diff key 'eigvec'
    """

    # Laplacian
    A = g.adjacency_matrix_scipy(return_edge_ids=False).astype(float)
    N = sp.diags(dgl.backend.asnumpy(g.in_degrees()).clip(1) ** -0.5, dtype=float)
    L = sp.eye(g.number_of_nodes()) - N * A * N

    # Eigenvectors with numpy
    EigVal, EigVec = np.linalg.eig(L.toarray())
    idx = EigVal.argsort() # increasing order
    EigVal, EigVec = EigVal[idx], np.real(EigVec[:,idx])
    g.ndata['eigvec'] = torch.from_numpy(EigVec[:,1:pos_enc_dim+1]).float() 

    # zero padding to the end if n < pos_enc_dim
    n = g.number_of_nodes()
    if n <= pos_enc_dim:
        g.ndata['eigvec'] = F.pad(g.ndata['eigvec'], (0, pos_enc_dim - n + 1), value=float('0'))

    return g


def lap_positional_encoding(g, pos_enc_dim):
    """
        Graph positional encoding v/ Laplacian eigenvectors
    """

    # Laplacian
    A = g.adjacency_matrix_scipy(return_edge_ids=False).astype(float)
    N = sp.diags(dgl.backend.asnumpy(g.in_degrees()).clip(1) ** -0.5, dtype=float)
    L = sp.eye(g.number_of_nodes()) - N * A * N

    # Eigenvectors with numpy
    EigVal, EigVec = np.linalg.eig(L.toarray())
    idx = EigVal.argsort() # increasing order
    EigVal, EigVec = EigVal[idx], np.real(EigVec[:,idx])
    g.ndata['pos_enc'] = torch.from_numpy(EigVec[:,1:pos_enc_dim+1]).float() 

    return g


def init_positional_encoding(g, pos_enc_dim, type_init):
    """
        Initializing positional encoding with RWPE
    """
    
    n = g.number_of_nodes()

    if type_init == 'rand_walk':
        # Geometric diffusion features with Random Walk
        A = g.adjacency_matrix(scipy_fmt="csr")
        Dinv = sp.diags(dgl.backend.asnumpy(g.in_degrees()).clip(1) ** -1.0, dtype=float) # D^-1
        RW = A * Dinv  
        M = RW
        
        # Iterate
        nb_pos_enc = pos_enc_dim
        PE = [torch.from_numpy(M.diagonal()).float()]
        M_power = M
        for _ in range(nb_pos_enc-1):
            M_power = M_power * M
            PE.append(torch.from_numpy(M_power.diagonal()).float())
        PE = torch.stack(PE,dim=-1)
        g.ndata['pos_enc'] = PE
    
    return g


def make_full_graph(graph, adaptive_weighting=None):
    g, label = graph

    full_g = dgl.from_networkx(nx.complete_graph(g.number_of_nodes()))

    # Copy over the node feature data and laplace  eigvecs
    full_g.ndata['feat'] = g.ndata['feat']
    
    try:
        full_g.ndata['pos_enc'] = g.ndata['pos_enc']
    except:
        pass
    
    try:
        full_g.ndata['eigvec'] = g.ndata['eigvec']
    except:
        pass

    # Initalize fake edge features w/ 0s
    full_g.edata['feat'] = torch.zeros(full_g.number_of_edges(), 3, dtype=torch.long)
    full_g.edata['real'] = torch.zeros(full_g.number_of_edges(), dtype=torch.long)

    # Copy real edge data over, and identify real edges!
    full_g.edges[g.edges(form='uv')[0].tolist(), g.edges(form='uv')[1].tolist()].data['feat'] = g.edata['feat']
    full_g.edges[g.edges(form='uv')[0].tolist(), g.edges(form='uv')[1].tolist()].data['real'] = torch.ones(
        g.edata['feat'].shape[0], dtype=torch.long)  # This indicates real edges

    # This code section only apply for GraphiT --------------------------------------------
    if adaptive_weighting is not None:
        p_steps, gamma = adaptive_weighting
    
        n = g.number_of_nodes()
        A = g.adjacency_matrix(scipy_fmt="csr")
        
        # Adaptive weighting k_ij for each edge
        if p_steps == "qtr_num_nodes":
            p_steps = int(0.25*n)
        elif p_steps == "half_num_nodes":
            p_steps = int(0.5*n)
        elif p_steps == "num_nodes":
            p_steps = int(n)
        elif p_steps == "twice_num_nodes":
            p_steps = int(2*n)

        N = sp.diags(dgl.backend.asnumpy(g.in_degrees()).clip(1) ** -0.5, dtype=float)
        I = sp.eye(n)
        L = I - N * A * N

        k_RW = I - gamma*L
        k_RW_power = k_RW
        for _ in range(p_steps - 1):
            k_RW_power = k_RW_power.dot(k_RW)

        k_RW_power = torch.from_numpy(k_RW_power.toarray())

        # Assigning edge features k_RW_eij for adaptive weighting during attention
        full_edge_u, full_edge_v = full_g.edges()
        num_edges = full_g.number_of_edges()

        k_RW_e_ij = []
        for edge in range(num_edges):
            k_RW_e_ij.append(k_RW_power[full_edge_u[edge], full_edge_v[edge]])

        full_g.edata['k_RW'] = torch.stack(k_RW_e_ij,dim=-1).unsqueeze(-1).float()
    # --------------------------------------------------------------------------------------
    
    return full_g, label

class OGBMOLDataset(Dataset):
    def __init__(self, name, features='full'):

        start = time.time()
        print("[I] Loading dataset %s..." % (name))
        self.name = name.lower()
        
        self.dataset = DglGraphPropPredDataset(name=self.name)
        
        if features == 'full':
            pass 
        elif features == 'simple':
            print("[I] Retaining only simple features...")
            # only retain the top two node/edge features
            for g in self.dataset.graphs:
                g.ndata['feat'] = g.ndata['feat'][:, :2]
                g.edata['feat'] = g.edata['feat'][:, :2]
        
        split_idx = self.dataset.get_idx_split()

        self.train = OGBMOLDGL(self.dataset, split_idx['train'])
        self.val = OGBMOLDGL(self.dataset, split_idx['valid'])
        self.test = OGBMOLDGL(self.dataset, split_idx['test'])
        
        self.evaluator = Evaluator(name=self.name)
        
        print("[I] Finished loading.")
        print("[I] Data load time: {:.4f}s".format(time.time()-start))

    # form a mini batch from a given list of samples = [(graph, label) pairs]
    def collate(self, samples):
        # The input samples is a list of pairs (graph, label).
        graphs, labels = map(list, zip(*samples))
        batched_graph = dgl.batch(graphs)
        labels = torch.stack(labels)
        tab_sizes_n = [ graphs[i].number_of_nodes() for i in range(len(graphs))]
        tab_snorm_n = [ torch.FloatTensor(size,1).fill_(1./float(size)) for size in tab_sizes_n ]
        snorm_n = torch.cat(tab_snorm_n).sqrt()
        
        return batched_graph, labels, snorm_n

    def _add_lap_positional_encodings(self, pos_enc_dim):

        # Graph positional encoding v/ Laplacian eigenvectors
        self.train = [(lap_positional_encoding(g, pos_enc_dim), label) for g, label in self.train]
        self.val = [(lap_positional_encoding(g, pos_enc_dim), label) for g, label in self.val]
        self.test = [(lap_positional_encoding(g, pos_enc_dim), label) for g, label in self.test]
        
    def _add_eig_vecs(self, pos_enc_dim):

        # Graph positional encoding v/ Laplacian eigenvectors
        self.train = [(add_eig_vec(g, pos_enc_dim), label) for g, label in self.train]
        self.val = [(add_eig_vec(g, pos_enc_dim), label) for g, label in self.val]
        self.test = [(add_eig_vec(g, pos_enc_dim), label) for g, label in self.test]
        
        
    def _init_positional_encodings(self, pos_enc_dim, type_init):

        # Initializing positional encoding randomly with l2-norm 1
        self.train = [(init_positional_encoding(g, pos_enc_dim, type_init), label) for g, label in self.train]
        self.val = [(init_positional_encoding(g, pos_enc_dim, type_init), label) for g, label in self.val]
        self.test = [(init_positional_encoding(g, pos_enc_dim, type_init), label) for g, label in self.test]
        
    def _make_full_graph(self, adaptive_weighting=None):
        self.train = [make_full_graph(graph, adaptive_weighting) for graph in self.train]
        self.val = [make_full_graph(graph, adaptive_weighting) for graph in self.val]
        self.test = [make_full_graph(graph, adaptive_weighting) for graph in self.test]
        

    